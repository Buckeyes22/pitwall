# Public Release Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close all 19 findings and the day-one checklist from the public-release review of
`release/squash` (`9cb06807`, Pitwall 0.3.0a1), then publish 0.3.0a1 as a new single-root public
repository.

**Architecture:** One integration branch carries every repository change (Tasks 1–11). Its tree is
exported as one parentless commit (Task 12) into a new public `Buckeyes22/pitwall` after the private
repository is renamed (Task 13). CI must pass on that exact root commit before the signed `v0.3.0a1`
tag runs the release workflow (Tasks 14–15).

**Tech Stack:** Python 3.14.7, uv 0.11.19, hatchling, GitHub Actions, GHCR, npm (Node 22.22.1),
Dependabot, `gh` CLI.

**Spec:** The release review findings 1–19 and the day-one checklist, reproduced as the
"Finding map" below. Line references are from `release/squash`. The working branch's tree is
byte-identical to it (`git diff --stat feat/zcode-harness release/squash` prints nothing).

## Decisions (fixed for this plan)

- **Agent execution default (F5):** an unset `PITWALL_AGENTS_UNRESTRICTED` means **restricted**.
  Unrestricted execution requires an explicit `PITWALL_AGENTS_UNRESTRICTED=1`. Kimi and dsh, which
  already refuse restricted runs, say so and name the `=1` opt-in.
- **Conduct channel (F18):** `chris@lateapexllc.com`. The mailbox or alias must exist and deliver
  before Task 13 completes.
- **Public repository name (F2, F4, F16):** rename the private repository to
  `Buckeyes22/pitwall-private`, then create a new public `Buckeyes22/pitwall`. The 91 tracked
  `Buckeyes22/pitwall` URLs remain correct. Reusing the name ends GitHub's rename redirect, which is
  intended.
- **Install channel (F1):** GitHub release wheel URL. No PyPI publishing in this release, and no
  bare `pitwall` package-name install anywhere in tracked files.
- **Commit identity for the public root:** `chris <chris@lateapexllc.com>` for author, committer,
  and DCO sign-off. This identity is already used in the staged export.

## Global Constraints

- Python only through `uv run` or `.venv/bin/python`. Never bare `python`.
- Setup: `uv sync --frozen --extra dev --python 3.14.7`.
- Work on one branch, `fix/public-release-review`, created from `feat/zcode-harness` (`d8ca2e01`,
  tree `f24ec9d0` = `release/squash^{tree}`). Use one PR into the private `main`. Never open several
  PRs that each run full CI.
- Every commit is signed off (`git commit -s`).
- Shim, hook, and `pitwall agents _shim`/`_steer-gate` paths must not import fastapi, uvicorn,
  asyncpg, redis, arq, textual, runpod, mcp, or prometheus_client
  (`tests/test_startup_imports.py`, `tests/agents/test_dispatch_import_weight.py`).
- `plugins/*/hooks/steer-gate.py` run without `pitwall` importable. They hold literal strings only.
- The Pi extensions under `src/pitwall/workbench/pi_extensions/` are not edited by this plan, and
  `make pi-extensions-check` must stay green.
- DB-backed suites (`make test-int`, the journey harness, coverage) share `pitwall_test` on
  `127.0.0.1:5444`. Run them one at a time, never in parallel.
- Tracked text must pass `tools/guards/repo_text_policy.py` with the local overlay
  (`PITWALL_TEXT_POLICY_EXTRA=tools/guards/repo_text_policy.local`). The overlay bans a private name
  pattern, so Task 8 narrows that rule to allow exactly the DCO sign-off address.
- Pipe long test output through `| tail -40`.
- This plan file names the private commit identity, which the local text-policy overlay bans. It
  stays **untracked** in the private checkout. Never `git add` it, so it is never in the tree
  Task 12 exports.
- Release-wheel install string for 0.3.0a1 (used verbatim everywhere):
  `uv tool install --python 3.14.7 https://github.com/Buckeyes22/pitwall/releases/download/v0.3.0a1/pitwall-0.3.0a1-py3-none-any.whl`
- Checkout install string: `uv tool install --python 3.14.7 .` from a clone at tag `v0.3.0a1`.

## Review Focus

1. **A user with the old default behavior relied on silently.** After Task 2, a Kimi or dsh
   dispatch with the variable unset must fail fast with a message naming
   `PITWALL_AGENTS_UNRESTRICTED=1`, and Codex must receive `--sandbox workspace-write`. Task 2 adds
   tests that pin both with the variable absent from the environment.
2. **A copied install command after a version bump.** If someone bumps the version without
   updating the wheel URLs, the release must fail before publishing. Task 1's validator rejects any
   `releases/download/v<X>/pitwall-<X>-` reference whose version differs from the candidate, and
   any bare package-name install.
3. **Checksum verification from a plain download directory.** `sha256sum -c SHA256SUMS` run in the
   directory where `gh release download` saved the assets must print `OK` for every line. Task 5
   pins the basename form in `tests/release/test_release_policy.py`.
4. **A dependency selected only through an extra.** `jsonschema[format]` → `rfc3987` is reachable
   only through `schemathesis[format]` in `dev`. Task 3's checker must find it in the `dev`
   profile and must not find it in `base`. Task 3 tests both with a fake distribution graph.
5. **A stranger following the README with two terminals.** The second terminal must get the same
   configuration. Task 9 pins in `tests/docs/test_readme_newcomer.py` that every terminal block
   needing configuration loads the same env file.

## Finding map

| Finding | Severity | Task(s) |
| --- | --- | --- |
| 1 PyPI name collision | BLOCKER | 1, 9 |
| 2 Verify the actual squash | BLOCKER | 12, 14 |
| 3 Broker quick start loses configuration | BLOCKER | 9 |
| 4 Private vulnerability reporting | BLOCKER | 13 |
| 5 Unrestricted agent default | SHOULD | 2, 8, 9 |
| 6 Routing prerequisites | SHOULD | 9 |
| 7 Spending boundaries | SHOULD | 9 |
| 8 Internal operational context | SHOULD | 7 |
| 9 License checker coverage | SHOULD | 3 |
| 10 Pi npm lock and inventory | SHOULD | 4 |
| 11 Third-party notice chain | SHOULD | 6 |
| 12 SBOM and legal evidence | SHOULD | 6 |
| 13 Non-affiliation wording | SHOULD | 6 (NOTICE), 9 (README) |
| 14 Release checksums and evidence | SHOULD | 5 |
| 15 Release runbooks | SHOULD | 8 |
| 16 GHCR access and visibility | SHOULD | 8 (runbook), 13, 15 |
| 17 Data-flow disclosure | SHOULD | 9 |
| 18 Contribution and support policies | NICE | 8 |
| 19 Dependabot configuration | NICE | 10 |
| Day-one checklist | — | 11–15 |

## File ownership (for parallel lanes)

Lanes start from `~/.claude/templates/lane-prompt.md`. At most five lanes run at once. Each lane
owns exactly the files listed. A file appears in one lane only.

| Lane | Tasks (in order) | Owns exclusively |
| --- | --- | --- |
| L1 agents | 1 → 2 | `src/pitwall/agents/**`, `src/pitwall/cost/notifications.py`, `plugins/**`, `scripts/release/validate_candidate.py`, `tests/agents/**`, `tests/release/test_install_references.py`, `docs/agents/**`, `docs/agent-routing/**`, `docs/operator/**`, `docs/prompting/**`, `docs/releases/**`, `docs/superpowers/specs/2026-09-28-single-project-design.md`, `docs/superpowers/plans/2026-09-28-single-project.md` |
| L2 supply chain | 3 → 4 → 10 | `tools/security/check_licenses.py`, `tools/security/license-policy.json`, `tests/security/test_license_policy.py`, `tools/pi-deps/**`, `tests/workbench/test_pi_deps_lock.py`, `.github/workflows/ci.yml`, `Makefile`, `.gitignore`, `tests/release/test_workflows.py`, `.github/dependabot.yml`, `docker/Dockerfile.*`, `tests/test_dockerfiles.py`, `tests/release_acceptance/test_ops_inventory.py`, `docs/legal/transitive-license-review.md` |
| L3 release workflow | 5 | `.github/workflows/release.yml`, `tests/release/test_release_policy.py` |
| L4 legal | 6 → 7 | `NOTICE`, `docs/legal/gateway-attribution-review.md`, `docs/legal/source-provenance-review.md`, `docs/legal/container-image-licenses.md`, `docs/sbom/**`, `tests/legal/**`, `docs/evidence/2026-09-26-broker-agent-gaps-live.md`, `docs/superpowers/plans/2026-09-26-broker-agent-gaps.md`, `docs/superpowers/plans/2026-09-02-pitwall-personal-first.md`, `docs/superpowers/specs/2026-09-02-pitwall-personal-first-design.md` |
| L5 policy and README | 8 → 9 | `RELEASING.md`, `docs/release/**`, `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SUPPORT.md`, `README.md`, `examples/prompts/**`, `tests/docs/test_readme_newcomer.py`, `tools/guards/repo_text_policy.local` (untracked) |

Every lane must not touch: other lanes' files, `.superpowers/`, `AGENTS.md`, `CLAUDE.md`,
`CHANGELOG.md` (the integrator edits it in Task 11), `pyproject.toml`, and `uv.lock`.
Tasks 11–15 run serially after all lanes merge.

---

### Task 1: Release-wheel install references and a guard (F1)

**Files:**
- Modify: `src/pitwall/agents/doctor.py:475` (`runtime.registered_hooks` remediation)
- Modify: `src/pitwall/agents/mcp_registration.py:125` (`channel_server_command`)
- Modify: `src/pitwall/cost/notifications.py:110`
- Modify: `plugins/claude/hooks/steer-gate.py:19-23`, `plugins/codex/hooks/steer-gate.py:19-23` (`RECOVERY`)
- Modify: `plugins/claude/README.md:35`, `plugins/codex/README.md`, `plugins/copilot/README.md`
- Modify: `docs/agent-routing/README.md:12`, `docs/agents/README.md:11`, `docs/agents/harness-cli-setup.md:41`,
  `docs/agents/install.md:165`, `docs/agents/orchestrator-channel.md:211`, `docs/agents/routing-readme.md:26,37`,
  `docs/operator/agents-migration.md:11,82`, `docs/operator/personal-serving.md:59,280`,
  `docs/operator/pi-workbench.md:13`, `docs/releases/v0.2.0a1.md:10,57`, `docs/releases/v0.3.0a1.md:54`,
  `docs/superpowers/plans/2026-09-28-single-project.md:1008,1669`,
  `docs/superpowers/specs/2026-09-28-single-project-design.md:10,152`
- Modify: `scripts/release/validate_candidate.py` (new `_install_reference_errors`, called from `validate`)
- Modify: `tests/agents/test_steer_gate_fail_closed.py:75`, `tests/agents/test_doctor_hooks.py:58`
- Create: `tests/release/test_install_references.py`

`README.md:139` is fixed in Task 9 because L5 owns README. The guard runs on all tracked files, so
Task 1's repo-wide guard test fails until Task 9 lands. Until then, run it with
`-k "not tracked_tree"`. Task 11 runs the full test.

**Interfaces:**
- Produces: `validate_candidate._install_reference_errors(version: str) -> list[str]` (called by
  `validate()`), plus module constants `BARE_INSTALL: re.Pattern[str]` and
  `RELEASE_WHEEL: re.Pattern[str]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/release/test_install_references.py
"""Install instructions never name the unrelated PyPI `pitwall` and track the release version."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _validator() -> ModuleType:
    path = ROOT / "scripts/release/validate_candidate.py"
    spec = importlib.util.spec_from_file_location("validate_candidate", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for relative, text in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    return tmp_path


@pytest.mark.parametrize(
    "line",
    [
        "uv tool install pitwall",
        "pip install pitwall",
        "pipx install pitwall`",
        "uv tool install --python 3.14.7 pitwall",
        "install pitwall[email] to enable email",
    ],
)
def test_bare_package_name_install_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, line: str
) -> None:
    module = _validator()
    monkeypatch.setattr(module, "ROOT", _repo(tmp_path, {"docs/a.md": line + "\n"}))
    assert module._install_reference_errors("0.3.0a1") == [
        f"docs/a.md:1: bare `pitwall` package install; PyPI's pitwall is an unrelated project"
    ]


@pytest.mark.parametrize(
    "line",
    [
        "claude plugin install pitwall@pitwall-local --scope user",
        "copilot plugin install pitwall-copilot@pitwall-local",
        "uv tool uninstall pitwall",
        "uv tool install --python 3.14.7 .",
        "uv tool install pitwall[email] @ https://example.invalid/pitwall-0.3.0a1-py3-none-any.whl",
    ],
)
def test_non_package_installs_are_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, line: str
) -> None:
    module = _validator()
    monkeypatch.setattr(module, "ROOT", _repo(tmp_path, {"docs/a.md": line + "\n"}))
    assert module._install_reference_errors("0.3.0a1") == []


def test_wheel_url_must_match_candidate_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _validator()
    stale = (
        "https://github.com/Buckeyes22/pitwall/releases/download/v0.2.0a1/"
        "pitwall-0.2.0a1-py3-none-any.whl\n"
    )
    monkeypatch.setattr(
        module,
        "ROOT",
        _repo(tmp_path, {"README.md": stale, "docs/releases/v0.2.0a1.md": stale}),
    )
    assert module._install_reference_errors("0.3.0a1") == [
        "README.md:1: release wheel URL names 0.2.0a1, expected 0.3.0a1"
    ]


def test_tracked_tree_has_no_bare_install_and_current_wheel_urls() -> None:
    assert _validator()._install_reference_errors("0.3.0a1") == []
```

Update the two existing assertions:

```python
# tests/agents/test_steer_gate_fail_closed.py:75
        self.assertIn(
            "! uv tool install --python 3.14.7 https://github.com/Buckeyes22/pitwall/releases/download/v",
            reason,
        )
# tests/agents/test_doctor_hooks.py:58
                self.assertIn(
                    "! uv tool install --python 3.14.7 https://github.com/Buckeyes22/pitwall/releases/download/v",
                    check.remediation or "",
                )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/release/test_install_references.py tests/agents/test_steer_gate_fail_closed.py tests/agents/test_doctor_hooks.py -p no:randomly -q | tail -20`
Expected: FAIL with `AttributeError: module 'validate_candidate' has no attribute '_install_reference_errors'` and two `assertIn` failures.

- [ ] **Step 3: Implement the validator**

Add to `scripts/release/validate_candidate.py` (imports `re` and `subprocess` at the top if absent):

```python
BARE_INSTALL = re.compile(
    r"\binstall (?:--\S+ (?:\S+ )?)*pitwall(?:\[[a-z,]+\])?(?=$|[`'\")\];,.]|\s(?!@))"
)
RELEASE_WHEEL = re.compile(r"releases/download/v([^/\s]+)/pitwall-([^-/\s]+)-py3-none-any\.whl")


def _install_reference_errors(version: str) -> list[str]:
    """Reject bare PyPI `pitwall` installs and wheel URLs for another version."""

    listed = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True
    ).stdout.decode("utf-8")
    errors: list[str] = []
    for relative in filter(None, listed.split("\0")):
        try:
            text = (ROOT / relative).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        historical = relative.startswith("docs/releases/") and relative != f"docs/releases/v{version}.md"
        for number, line in enumerate(text.splitlines(), start=1):
            if BARE_INSTALL.search(line):
                errors.append(
                    f"{relative}:{number}: bare `pitwall` package install; "
                    "PyPI's pitwall is an unrelated project"
                )
            if historical or relative == "CHANGELOG.md":
                continue
            for tag, wheel in RELEASE_WHEEL.findall(line):
                if tag != version or wheel != version:
                    errors.append(
                        f"{relative}:{number}: release wheel URL names {tag}, expected {version}"
                    )
    return errors
```

In `validate()`, next to `errors.extend(_plugin_errors(version))` (line 90), add
`errors.extend(_install_reference_errors(version))`.

- [ ] **Step 4: Replace every bare install**

Use the release-wheel install string from Global Constraints. Exact replacements:

- `plugins/claude/hooks/steer-gate.py` and `plugins/codex/hooks/steer-gate.py` `RECOVERY`. Keep
  the URL on one line (`# noqa: E501`), so the validator's per-line version check sees it:

```python
RECOVERY = (
    "Do not try to fix this with a tool call. Type "
    "`! uv tool install --python 3.14.7 https://github.com/Buckeyes22/pitwall/releases/download/v0.3.0a1/pitwall-0.3.0a1-py3-none-any.whl` "  # noqa: E501
    "at the prompt to reinstall the CLI, or disable the Pitwall plugin with /plugin. "
    "Check the install afterwards with `pitwall doctor`."
)
```

- `src/pitwall/agents/doctor.py:474-476` remediation: use the same one-line URL string as the
  hooks, followed by `", or disable the plugin with /plugin"`.
- `src/pitwall/agents/mcp_registration.py:125`: the message becomes
  `"pitwall is not on PATH; install it (`uv tool install --python 3.14.7 <one-line release wheel URL>`) before registering the channel server"`.
- `src/pitwall/cost/notifications.py:110`:
  `"resend package is not installed; reinstall Pitwall with the email extra: uv tool install --python 3.14.7 'pitwall[email] @ https://github.com/Buckeyes22/pitwall/releases/download/v0.3.0a1/pitwall-0.3.0a1-py3-none-any.whl'"`.
  This is the PEP 508 direct-reference form. The quote before `pitwall` keeps `BARE_INSTALL`
  from matching, and the lookahead also rejects a following ` @`.
- Each listed doc line: replace `uv tool install pitwall` with the release-wheel install string,
  or in prose, with "install the release wheel (see the Quick Start in `README.md`)".
  Use a relative link from the file's directory. In `docs/releases/v0.2.0a1.md`, use
  `uv tool install --python 3.14.7 .` from a checkout of `v0.2.0a1`, because that release
  published no wheel URL.
- `docs/superpowers/specs/2026-09-28-single-project-design.md:10`: replace
  `` `uv tool install pitwall` (or `pip install pitwall`) `` with "installing the release wheel".

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/release/test_install_references.py tests/agents/test_steer_gate_fail_closed.py tests/agents/test_doctor_hooks.py tests/release/test_release_policy.py -p no:randomly -q -k "not tracked_tree" | tail -5`
Expected: all pass.
Run: `git ls-files | grep -v '^README.md$' | xargs grep -nE "\binstall (--[^ ]+ ([^ ]+ )?)*pitwall(\[[a-z,]+\])?([[:space:]\`'\")\];,.]|$)" ; echo exit=$?`
Expected: no matches, `exit=1`.
Run: `uv run python tools/ci/check_markdown_links.py | tail -3`
Expected: link check passes.

- [ ] **Step 6: Commit**

```bash
git add scripts/release/validate_candidate.py tests/release/test_install_references.py tests/agents src/pitwall plugins
git add -u docs   # tracked docs only; leaves this untracked plan out
git commit -s -m "fix(install): point every install at the GitHub release wheel; PyPI's pitwall is unrelated"
```

---

### Task 2: Restricted harness execution by default (F5)

**Files:**
- Modify: `src/pitwall/agents/harnesses/base.py:249-250` (`Harness.unrestricted`)
- Modify: `src/pitwall/agents/harnesses/kimi.py:45-52`, `src/pitwall/agents/harnesses/dsh.py:45-52` (refusal text)
- Modify: `src/pitwall/agents/harnesses/agy.py:90-94` (re-run hint)
- Modify: `src/pitwall/agents/doctor.py:1580-1627` (`_check_security_unrestricted_mode`)
- Modify: `docs/agents/routing-readme.md:50,396,483`, `docs/agents/SECURITY.md:23`, `docs/agents/zcode.md:21`,
  `docs/prompting/*.md` lines naming the default, `plugins/{claude,codex,copilot}/skills/subagent-model-routing/**` lines naming the default
- Test: `tests/agents/test_provider_adapters.py`, `tests/agents/test_doctor.py`, and every test that relied on the implicit default (Step 4)

**Interfaces:**
- Produces: `Harness.unrestricted(env) -> bool` returns `True` only for `env["PITWALL_AGENTS_UNRESTRICTED"] == "1"`.
  `policy_profile` is unchanged.

- [ ] **Step 1: Write the failing tests**

Add to `tests/agents/test_provider_adapters.py`:

```python
class UnsetUnrestrictedDefaultTests(unittest.TestCase):
    """An unset PITWALL_AGENTS_UNRESTRICTED keeps each CLI's own sandbox and approvals."""

    def test_codex_unset_runs_workspace_write_sandbox(self) -> None:
        adapter = load_adapter("codex")
        request = parse_request(adapter, ["prompt.md"])
        prepared = adapter.prepare(request, "/bin/codex", b"prompt\n", {}, {})
        self.assertIn("--sandbox", prepared.argv)
        self.assertIn("workspace-write", prepared.argv)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", prepared.argv)
        self.assertEqual(adapter.policy_profile({}), "cli-policy")

    def test_claude_unset_keeps_permission_prompts(self) -> None:
        adapter = load_adapter("claude")
        request = parse_request(adapter, ["prompt.md"])
        prepared = adapter.prepare(request, "/bin/claude", b"prompt\n", {}, {})
        self.assertNotIn("--dangerously-skip-permissions", prepared.argv)

    def test_kimi_and_dsh_unset_refuse_and_name_the_opt_in(self) -> None:
        for harness in ("kimi", "dsh"):
            with self.subTest(harness=harness):
                adapter = load_adapter(harness)
                request = parse_request(adapter, ["prompt.md"])
                with self.assertRaisesRegex(UsageError, "PITWALL_AGENTS_UNRESTRICTED=1"):
                    adapter.prepare(request, f"/bin/{harness}", b"prompt\n", {}, {})

    def test_explicit_one_still_bypasses(self) -> None:
        adapter = load_adapter("codex")
        request = parse_request(adapter, ["prompt.md"])
        prepared = adapter.prepare(
            request, "/bin/codex", b"prompt\n", {"PITWALL_AGENTS_UNRESTRICTED": "1"}, {}
        )
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", prepared.argv)
```

`load_adapter`, `parse_request`, and `UsageError` are the helpers this module already uses (see
`RESTRICTED_ENV` at line 1229 and its users). If kimi or dsh raise in `preflight` rather than
`prepare`, call the same method that the existing line-1286 test calls.

Add to `tests/agents/test_doctor.py`:

```python
def test_unrestricted_mode_unset_passes_as_restricted() -> None:
    check = doctor._check_security_unrestricted_mode(Path("."), {})
    assert check.status == "PASS"
    assert "restricted" in check.summary
    assert check.details == {"value": None}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_provider_adapters.py -k UnsetUnrestricted tests/agents/test_doctor.py -k unrestricted_mode_unset -p no:randomly -q | tail -15`
Expected: FAIL. Codex argv contains `--dangerously-bypass-approvals-and-sandbox`, and the doctor status is `WARN`.

- [ ] **Step 3: Implement**

```python
# src/pitwall/agents/harnesses/base.py
    @staticmethod
    def unrestricted(env: Mapping[str, str]) -> bool:
        """Bypass the child CLI's sandbox and approvals only on an explicit opt-in."""
        return env.get("PITWALL_AGENTS_UNRESTRICTED") == "1"
```

In `kimi.py` and `dsh.py`, end the refusal message with
`"; set PITWALL_AGENTS_UNRESTRICTED=1 to dispatch through <harness>"`. This replaces the current
`"unset PITWALL_AGENTS_UNRESTRICTED=0 ..."` wording. Update the existing regex at
`tests/agents/test_provider_adapters.py:1286` to `"PITWALL_AGENTS_UNRESTRICTED=1"`.

In `agy.py:92-93`, replace `"(leave PITWALL_AGENTS_UNRESTRICTED unset)"` with
`"(set PITWALL_AGENTS_UNRESTRICTED=1)"`.

`doctor._check_security_unrestricted_mode`: the `value is None` branch returns `status="PASS"`,
`summary="PITWALL_AGENTS_UNRESTRICTED is unset; harness CLIs keep their own sandbox and approval policy (restricted)"`,
and no remediation. The `"1"` branch keeps `WARN` with
`summary="PITWALL_AGENTS_UNRESTRICTED=1 bypasses harness sandboxes and approval prompts"`.

- [ ] **Step 4: Make implicit-default tests explicit**

Run: `uv run pytest tests/agents -n auto -q -m "not integration and not slow" | tail -40`.
For every failure that asserts unrestricted flags (`--dangerously-*`, `--yolo`, `yolo`,
`--approve`, `--always-approve`, `"unrestricted"` policy profile), add
`PITWALL_AGENTS_UNRESTRICTED="1"` to that test's environment
(`sandbox.environment(PITWALL_AGENTS_UNRESTRICTED="1")`, or the dict passed to `prepare`). Do not
change `tests/agents/shim_test_support.py::ShimSandbox.environment`. A neutral sandbox is what
proves the new default. Re-run until green.

- [ ] **Step 5: Update docs that state the default**

Find every line with: `git grep -n "PITWALL_AGENTS_UNRESTRICTED" -- docs plugins | grep -v docs/superpowers`.
Restate the default as restricted:
- `docs/agents/routing-readme.md:50`: "By default the shims keep each dispatched CLI's own sandbox and approval prompts. Unattended runs that must not stop for approval need an explicit `PITWALL_AGENTS_UNRESTRICTED=1`, which passes the CLI's bypass flag (for Codex, `--dangerously-bypass-approvals-and-sandbox`). Kimi Code and dsh refuse to dispatch without it because their prompt modes have no restricted form."
- `routing-readme.md:396` table default column: `0 (unset)`.
- `routing-readme.md:483` and `docs/agents/SECURITY.md:23`: same meaning, restricted unless `=1`.
- Skill and prompting references: "Set `PITWALL_AGENTS_UNRESTRICTED=1` only when ..." in place of "Set it to `0` to retain ...".

- [ ] **Step 6: Run the agents suite, import-weight tests, and doctor**

Run: `uv run pytest tests/agents tests/test_startup_imports.py -n auto -q -m "not integration and not slow" | tail -5`
Expected: all pass.
Run: `env -u PITWALL_AGENTS_UNRESTRICTED uv run pitwall agents doctor --json | uv run python -c "import json,sys; d=json.load(sys.stdin); print([c['status'] for c in d['checks'] if c['id']=='security.unrestricted_mode'])"`
Expected: `['PASS']`. If the JSON top-level key differs, adjust the key to the shape that
`pitwall agents doctor --json` prints.

- [ ] **Step 7: Commit**

```bash
git add src/pitwall/agents tests/agents docs/agents docs/prompting plugins
git commit -s -m "fix(agents): keep harness sandboxes unless PITWALL_AGENTS_UNRESTRICTED=1 is explicit"
```

- [ ] **Step 8: Maintainer workstation**

This workstation dispatches unattended through Kimi, Codex, and others, so it must keep its
current behavior. Add `export PITWALL_AGENTS_UNRESTRICTED=1` to the shell profile that launches
the coding-agent hosts. Run: `bash -lc 'echo $PITWALL_AGENTS_UNRESTRICTED'` → `1`.

---

### Task 3: License inventory per install profile (F9)

**Files:**
- Modify: `tools/security/check_licenses.py` (`runtime_graph`, `evaluate`, `main`)
- Modify: `tools/security/license-policy.json` (add `profile_review_required`, add `"GPLv"` to `denied_license_terms`)
- Modify: `tests/security/test_license_policy.py`
- Modify: `.github/workflows/ci.yml:286-288` (security-sast job sync and license steps)
- Modify: `Makefile:36-37` (`license-check`)
- Modify: `docs/legal/transitive-license-review.md`
- Test: `tests/test_dockerfiles.py` (add the container-profile pin)

**Interfaces:**
- Produces: `runtime_graph(root_name: str, extras: frozenset[str] = frozenset()) -> list[Distribution]`
  and `evaluate(rows, policy, profile: str = "base") -> list[str]`. CLI: `--extra NAME` (repeatable)
  and `--npm-lock PATH` (Task 4 adds the npm reader to the same file).

- [ ] **Step 1: Write the failing tests**

Append to `tests/security/test_license_policy.py`:

```python
class _FakeDist:
    def __init__(self, name: str, requires: list[str]) -> None:
        self.metadata = {"Name": name}
        self.requires = requires
        self.version = "1"


def _graph(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = _module()
    dists = {
        "pitwall": _FakeDist("pitwall", ['schemathesis[format]; extra == "dev"', "httpx"]),
        "httpx": _FakeDist("httpx", []),
        "schemathesis": _FakeDist("schemathesis", ['jsonschema[format]; extra == "format"']),
        "jsonschema": _FakeDist("jsonschema", ['rfc3987; extra == "format"']),
        "rfc3987": _FakeDist("rfc3987", []),
    }
    monkeypatch.setattr(module, "distribution", lambda name: dists[name])
    return module


def test_base_profile_excludes_extra_only_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    names = [d.metadata["Name"] for d in _graph(monkeypatch).runtime_graph("pitwall")]
    assert names == ["httpx", "pitwall"]


def test_extra_profile_propagates_requested_extras_transitively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _graph(monkeypatch)
    names = [d.metadata["Name"] for d in module.runtime_graph("pitwall", frozenset({"dev"}))]
    assert names == ["httpx", "jsonschema", "pitwall", "rfc3987", "schemathesis"]


def test_profile_review_accepts_reviewed_dev_only_gpl() -> None:
    policy = _policy() | {
        "denied_license_terms": ["GPLv"],
        "profile_review_required": {
            "dev": {"rfc3987": {"version": "1.3.8", "license": "GNU GPLv3+"}}
        },
    }
    rows = [{"name": "rfc3987", "version": "1.3.8", "license": "GNU GPLv3+"},
            {"name": "special", "version": "2", "license": "MPL-2.0"}]
    assert _module().evaluate(rows, policy, profile="dev") == []
    assert _module().evaluate(rows, policy, profile="base") == [
        "rfc3987 1.3.8: denied license 'GNU GPLv3+'"
    ]
```

Add `import pytest` and `from types import ModuleType` at the top if absent. `_policy()` already
exists in the file.

Add to `tests/test_dockerfiles.py`:

```python
@pytest.mark.parametrize("name", sorted(IMAGES))
def test_images_install_the_base_license_profile_only(name: str) -> None:
    builder = _stages(name)["builder"]
    assert "--no-dev" in builder
    assert "--extra" not in builder and "--all-extras" not in builder
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/security/test_license_policy.py tests/test_dockerfiles.py -p no:randomly -q | tail -15`
Expected: the extra-propagation test fails (`TypeError: runtime_graph() takes 1 positional argument`),
and the profile test fails (`evaluate() got an unexpected keyword argument 'profile'`).

- [ ] **Step 3: Implement**

```python
def runtime_graph(root_name: str, extras: frozenset[str] = frozenset()) -> list[Distribution]:
    """Resolve installed dependencies from one root, propagating requested extras transitively."""

    start = (canonicalize_name(root_name), frozenset(canonicalize_name(e) for e in extras))
    queue = deque([start])
    visited: set[tuple[str, frozenset[str]]] = set()
    found: dict[str, Distribution] = {}
    while queue:
        name, active = queue.popleft()
        if (name, active) in visited:
            continue
        visited.add((name, active))
        try:
            dist = distribution(name)
        except PackageNotFoundError as exc:
            raise RuntimeError(f"dependency is not installed: {name}") from exc
        found[name] = dist
        for raw_requirement in dist.requires or []:
            requirement = Requirement(raw_requirement)
            if requirement.marker is not None and not any(
                requirement.marker.evaluate({"extra": extra}) for extra in ("", *sorted(active))
            ):
                continue
            queue.append(
                (
                    canonicalize_name(requirement.name),
                    frozenset(canonicalize_name(e) for e in requirement.extras),
                )
            )
    return sorted(found.values(), key=lambda item: canonicalize_name(item.metadata["Name"]))
```

`evaluate(rows, policy, profile="base")`: build `review` from `policy["review_required_packages"]`
merged with `policy.get("profile_review_required", {}).get(profile, {})`. Check review entries
**before** denied terms, so an exact reviewed version and license is accepted and any drift is
still reported. Unreviewed rows keep the existing denied, unknown, and allowed checks. Report a
missing review entry only for names in the merged `review`.

`main()`: add `parser.add_argument("--extra", action="append", default=[])`. Set
`profile = "+".join(sorted(args.extra)) or "base"` and pass `frozenset(args.extra)` and `profile`
through. Add `"profile": profile` to the report.

Policy additions:

```json
  "profile_review_required": {
    "dev": {
      "rfc3987": {
        "version": "1.3.8",
        "license": "GNU GPLv3+",
        "distributed": false,
        "reason": "test-only, via schemathesis[format] -> jsonschema[format]; absent from the wheel, sdist, and images (uv export --frozen --no-dev --all-extras has no rfc3987)"
      }
    }
  },
```

Also add `"GPLv"` to `denied_license_terms`.

- [ ] **Step 4: Inventory every profile and record what it finds**

Run:
```bash
uv sync --frozen --all-extras --python 3.14.7
uv run python tools/security/check_licenses.py --report /tmp/lic-base.json; echo base=$?
uv run python tools/security/check_licenses.py --extra storage --extra email --extra tracing --report /tmp/lic-extras.json; echo extras=$?
uv run python tools/security/check_licenses.py --extra dev --report /tmp/lic-dev.json; echo dev=$?
uv export --frozen --no-dev --all-extras --no-hashes | grep -c rfc3987
```
Expected: `base=0`, `extras=0`, `dev=0`, and a final count of `0`. A non-zero profile prints
`license policy failed: <name> <version>: ...`. For each such line, read the package's license on
its PyPI page and add an exact entry to the matching profile under `profile_review_required`, with
`distributed` and `reason`. Never widen `allowed_license_terms` for one package. Re-run until all
three exit 0.

`docs/legal/transitive-license-review.md`: add a section "Install profiles (0.3.0a1)" with one
row per profile (base = wheel runtime and all five images, extras, dev). Each row gives the
package count from the report and the reviewed exceptions. State that `rfc3987` (GPLv3+) is a
test-only dev dependency that is not redistributed, so its presence in `uv.lock` places no
license obligation on Pitwall's distributed artifacts.

- [ ] **Step 5: Wire CI and Make**

In `.github/workflows/ci.yml` security-sast job, change `run: uv sync --frozen --extra dev` to
`run: uv sync --frozen --all-extras`. Replace the single license step with:

```yaml
      - name: Dependency license policy (base runtime and images, extras, dev)
        run: |
          uv run python tools/security/check_licenses.py
          uv run python tools/security/check_licenses.py --extra storage --extra email --extra tracing
          uv run python tools/security/check_licenses.py --extra dev
```

Make the `Makefile` `license-check` recipe run the same three commands.

- [ ] **Step 6: Verify**

Run: `uv run pytest tests/security/test_license_policy.py tests/test_dockerfiles.py tests/release/test_workflows.py -p no:randomly -q | tail -5` → all pass.
Run: `make license-check > /dev/null; echo $?` → `0`.

- [ ] **Step 7: Commit**

```bash
git add tools/security tests/security/test_license_policy.py tests/test_dockerfiles.py .github/workflows/ci.yml Makefile docs/legal/transitive-license-review.md
git commit -s -m "fix(security): license inventory per install profile with transitive extras"
```

---

### Task 4: Locked, license-checked Pi npm dependencies (F10)

**Files:**
- Create: `tools/pi-deps/package.json`, `tools/pi-deps/package-lock.json`
- Modify: `tools/security/check_licenses.py` (`npm_lock_rows`, `--npm-lock`)
- Modify: `tools/security/license-policy.json` (`profile_review_required.npm` as found)
- Modify: `.github/workflows/ci.yml:366-372`, `:433-444`, `:523-529` (three npm install steps)
- Modify: `Makefile` (`license-check` adds the npm profile)
- Modify: `.gitignore` (`tools/pi-deps/node_modules/`)
- Create: `tests/workbench/test_pi_deps_lock.py`
- Modify: `tests/security/test_license_policy.py`

**Interfaces:**
- Consumes: `evaluate(rows, policy, profile)` from Task 3, and `pitwall.workbench.pi_pin.PINNED_PI_VERSION` / `PINNED_SUBAGENTS_VERSION`.
- Produces: `npm_lock_rows(lock: dict[str, Any]) -> list[dict[str, str]]`, and the CLI
  `check_licenses.py --npm-lock tools/pi-deps/package-lock.json` (profile `npm`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/workbench/test_pi_deps_lock.py
"""CI's Pi test dependencies are locked and match the installer pins."""

from __future__ import annotations

import json
from pathlib import Path

from pitwall.workbench.pi_pin import PINNED_PI_VERSION, PINNED_SUBAGENTS_VERSION

ROOT = Path(__file__).resolve().parents[2]
DEPS = ROOT / "tools" / "pi-deps"


def test_manifest_pins_the_installer_versions_exactly() -> None:
    deps = json.loads((DEPS / "package.json").read_text(encoding="utf-8"))["dependencies"]
    assert deps == {
        "@earendil-works/pi-ai": PINNED_PI_VERSION,
        "@earendil-works/pi-coding-agent": PINNED_PI_VERSION,
        "@earendil-works/pi-tui": PINNED_PI_VERSION,
        "@tintinweb/pi-subagents": PINNED_SUBAGENTS_VERSION,
    }


def test_lock_resolves_the_manifest_and_records_integrity() -> None:
    lock = json.loads((DEPS / "package-lock.json").read_text(encoding="utf-8"))
    assert lock["lockfileVersion"] == 3
    packages = lock["packages"]
    assert packages["node_modules/@earendil-works/pi-coding-agent"]["version"] == PINNED_PI_VERSION
    for path, entry in packages.items():
        if path and not entry.get("link"):
            assert entry.get("integrity", "").startswith("sha512-"), path


def test_ci_installs_from_the_lock_only() -> None:
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "--no-package-lock" not in ci
    assert ci.count("npm ci --prefix tools/pi-deps --ignore-scripts") == 3
```

Append to `tests/security/test_license_policy.py`:

```python
def test_npm_lock_rows_read_name_version_license() -> None:
    lock = {"packages": {
        "": {"name": "root"},
        "node_modules/@scope/a": {"version": "1.0.0", "license": "MIT"},
        "node_modules/b/node_modules/c": {"version": "2.0.0"},
    }}
    assert _module().npm_lock_rows(lock) == [
        {"name": "@scope/a", "version": "1.0.0", "license": "MIT"},
        {"name": "c", "version": "2.0.0", "license": "UNKNOWN"},
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/workbench/test_pi_deps_lock.py tests/security/test_license_policy.py -p no:randomly -q | tail -10`
Expected: FAIL. `FileNotFoundError` for `tools/pi-deps/package.json`, and `npm_lock_rows` is missing.

- [ ] **Step 3: Create the manifest and lock**

```json
{
  "name": "pitwall-pi-test-deps",
  "private": true,
  "description": "Pinned Pi packages that CI's workbench tests and extension check load. Not shipped.",
  "license": "Apache-2.0",
  "dependencies": {
    "@earendil-works/pi-ai": "0.84.4",
    "@earendil-works/pi-coding-agent": "0.84.4",
    "@earendil-works/pi-tui": "0.84.4",
    "@tintinweb/pi-subagents": "0.19.0"
  }
}
```

Run with Node 22.22.1: `npm install --prefix tools/pi-deps --package-lock-only --ignore-scripts`.
Expected: `tools/pi-deps/package-lock.json` is created with `"lockfileVersion": 3`.
Add `tools/pi-deps/node_modules/` to `.gitignore`.

- [ ] **Step 4: Add the npm reader**

```python
def npm_lock_rows(lock: dict[str, Any]) -> list[dict[str, str]]:
    """One row per installed package in an npm lockfile v3."""

    rows: list[dict[str, str]] = []
    for path, entry in sorted(lock["packages"].items()):
        if not path or entry.get("link"):
            continue
        name = entry.get("name") or path.rsplit("node_modules/", 1)[1]
        license_value = entry.get("license")
        rows.append({
            "name": name,
            "version": str(entry.get("version", "")),
            "license": str(license_value) if license_value else "UNKNOWN",
        })
    return rows
```

In `main()`, add `parser.add_argument("--npm-lock", type=Path)`. When it is given, set
`rows = npm_lock_rows(json.loads(args.npm_lock.read_text(encoding="utf-8")))` and
`profile = "npm"`, and skip `runtime_graph`. Use the same `evaluate`, report, and exit code.
Python's `canonicalize_name` in `evaluate` lowercases names. Key npm review entries with their
lowercased form (`@scope/name`).

- [ ] **Step 5: Inventory the npm graph**

Run: `uv run python tools/security/check_licenses.py --npm-lock tools/pi-deps/package-lock.json --report /tmp/lic-npm.json; echo npm=$?`
Expected after review: `npm=0`. For each `license policy failed` line, read the license on the
package's npm page or in its repository `LICENSE`. Add an exact
`profile_review_required.npm["<name>"]` entry with `version`, `license`, `distributed: false`, and
`reason: "CI test dependency; not shipped"`. A package whose lock entry has no `license` field
gets the license you verified, and that same string is set in the review entry. Then add the row
"npm (CI only)" to the profile table in `docs/legal/transitive-license-review.md`.

- [ ] **Step 6: Switch CI to the lock**

Replace each of the three `npm install --prefix "$RUNNER_TEMP/pi" ... --no-package-lock ...`
blocks (`.github/workflows/ci.yml:366-372`, `:433-444`, `:523-529`) with:

```yaml
        run: |
          npm ci --prefix tools/pi-deps --ignore-scripts
          echo "PITWALL_PI_MODULES=$GITHUB_WORKSPACE/tools/pi-deps/node_modules" >> "$GITHUB_ENV"
```

In the `pi-extensions` job, keep the existing `for package in ...; do test -d ...` existence
loop, with its path changed to `tools/pi-deps/node_modules/$package`. Add to the security-sast
license step: `uv run python tools/security/check_licenses.py --npm-lock tools/pi-deps/package-lock.json`.
Add the same line to `make license-check`.

- [ ] **Step 7: Verify**

Run: `uv run pytest tests/workbench/test_pi_deps_lock.py tests/security/test_license_policy.py tests/release/test_workflows.py -p no:randomly -q | tail -5` → all pass.
Run: `npm ci --prefix tools/pi-deps --ignore-scripts && PITWALL_PI_MODULES=$PWD/tools/pi-deps/node_modules uv run pytest tests/workbench/pi_extensions -p no:randomly -q | tail -3` → pass.
Run: `make pi-extensions-check` → `pi-extensions-check: committed .js matches the compiler output`.

- [ ] **Step 8: Commit**

```bash
git add tools/pi-deps/package.json tools/pi-deps/package-lock.json tools/security tests/workbench/test_pi_deps_lock.py tests/security/test_license_policy.py .github/workflows/ci.yml Makefile .gitignore docs/legal/transitive-license-review.md
git commit -s -m "fix(ci): install Pi test packages from a lockfile and license-check the npm graph"
```

---

### Task 5: Basename checksums and public image evidence in the release (F14)

**Files:**
- Modify: `.github/workflows/release.yml:39-42` (package job checksums), `:211-246` (publish-image evidence), `:248-276` (github-release downloads)
- Modify: `tests/release/test_release_policy.py` (`test_github_first_release_requires_ghcr_but_not_deferred_pypi` and a new test)

**Interfaces:**
- Produces: per-service artifacts `image-evidence-<service>` that contain `<service>.spdx.json`,
  `<service>.digest` (`ghcr.io/<owner>/pitwall/<service>@sha256:...`), and
  `<service>.staging-digest`. The release assets are the wheel, the sdist, `SHA256SUMS`, the
  package SPDX, five image SPDX files, five digest files, and `compose-smoke` evidence.

- [ ] **Step 1: Write the failing test**

```python
def test_release_assets_verify_with_plain_sha256sum_and_carry_image_evidence() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    package = workflow.split("  package:", 1)[1].split("\n  package-provenance:", 1)[0]
    assert "(cd dist && sha256sum -- *) > artifacts/package/SHA256SUMS" in package
    assert "sha256sum dist/*" not in workflow
    publish = workflow.split("  publish-image:", 1)[1].split("\n  github-release:", 1)[0]
    assert "name: image-evidence-${{ matrix.service }}" in publish
    github_release = workflow.split("  github-release:", 1)[1]
    assert "pattern: image-evidence-*" in github_release
    assert "name: compose-smoke-evidence" in github_release
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/release/test_release_policy.py -k sha256sum -p no:randomly -q | tail -5`
Expected: FAIL on the `cd dist && sha256sum` assertion.

- [ ] **Step 3: Implement**

Package job "Generate checksums" step:

```yaml
      - name: Generate checksums
        # Basenames, so `sha256sum -c SHA256SUMS` works beside downloaded release assets.
        run: |
          mkdir -p artifacts/package
          (cd dist && sha256sum -- *) > artifacts/package/SHA256SUMS
```

`package-provenance` uses `subject-checksums: evidence/SHA256SUMS`. With basenames, the
attestation subjects become the asset file names, which is what
`gh attestation verify <file> --repo Buckeyes22/pitwall` matches by digest.

In `publish-image`, after the attest step, add:

```yaml
      - uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4
        with:
          name: staged-image-${{ matrix.service }}
          path: artifacts/staged
      - name: Collect public image evidence
        run: |
          mkdir -p artifacts/evidence
          cp artifacts/image/${{ matrix.service }}.spdx.json artifacts/evidence/
          printf '%s@%s\n' "${{ steps.push.outputs.name }}" "${{ steps.push.outputs.digest }}" \
            > artifacts/evidence/${{ matrix.service }}.digest
          cp artifacts/staged/${{ matrix.service }}.staging-digest artifacts/evidence/
      - uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4
        with:
          name: image-evidence-${{ matrix.service }}
          path: artifacts/evidence/
          if-no-files-found: error
          retention-days: 14
```

In `github-release`, after the two existing downloads, add:

```yaml
      - uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4
        with:
          pattern: image-evidence-*
          path: release-artifacts
          merge-multiple: true
      - uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4
        with:
          name: compose-smoke-evidence
          path: release-artifacts/compose-smoke
```

`files: release-artifacts/**` already uploads every file. Because each filename is
service-prefixed, merge-multiple cannot collide. Check the compose-smoke file names with
`grep -n "PITWALL_SMOKE_ARTIFACT_DIR" -A3 scripts/release/smoke_compose.sh`. If two files there
share a basename, prefix them with `compose-smoke-` in a `run` step before the release step,
because the release action flattens directories.

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/release -p no:randomly -q | tail -5` → all pass.
Run: `uvx --from actionlint-py==1.7.7.23 actionlint .github/workflows/release.yml; echo $?` → `0`.
If the repository pins actionlint elsewhere, use the version from `.github/actionlint.yaml`/CI.
Run a local checksum round trip:
`uv build --wheel --sdist --out-dir /tmp/rel/dist && (cd /tmp/rel/dist && sha256sum -- *) > /tmp/rel/SHA256SUMS && cp /tmp/rel/dist/* /tmp/rel/ && (cd /tmp/rel && sha256sum -c SHA256SUMS)`
→ two `OK` lines.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/release.yml tests/release/test_release_policy.py
git commit -s -m "fix(release): basename checksums; attach image SBOMs, digests, and smoke evidence"
```

---

### Task 6: Notice chain, SBOM, provenance, and NOTICE non-affiliation (F11, F12, F13)

**Files:**
- Modify: `NOTICE`
- Modify: `docs/legal/gateway-attribution-review.md`, `docs/legal/source-provenance-review.md`
- Create: `docs/legal/container-image-licenses.md`
- Modify: `docs/sbom/pitwall-sbom.cdx.json` (regenerated), `docs/sbom/pitwall-sbom.md`
- Create: `tests/legal/test_notice_and_sbom.py`

**Interfaces:**
- Consumes: nothing from earlier tasks. NOTICE is already in `license-files` (`pyproject.toml:10`),
  so it lands in the wheel's `.dist-info/licenses/`, in the sdist (`/NOTICE`), and in all five
  images through the installed wheel (`docker/Dockerfile.*` builds and installs the wheel).

- [ ] **Step 1: Write the failing tests**

```python
# tests/legal/test_notice_and_sbom.py
"""NOTICE carries every third-party permission notice; the SBOM describes this version."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NOTICE = (ROOT / "NOTICE").read_text(encoding="utf-8")


def test_notice_reproduces_the_omniroute_mit_notice() -> None:
    lock = json.loads((ROOT / "config/gateway-catalog.lock.json").read_text(encoding="utf-8"))
    assert NOTICE.count("Permission is hereby granted, free of charge") == 2
    assert "diegosouzapw" in NOTICE
    assert lock["tarball_sha256"] in NOTICE


def test_notice_disclaims_affiliation_with_every_named_provider() -> None:
    for name in ("RunPod", "OpenAI", "Anthropic", "Google", "GitHub", "Vast.ai", "Together", "Lambda"):
        assert name in NOTICE, name
    assert "interoperability" in NOTICE


def test_committed_sbom_describes_the_current_version_and_lock() -> None:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    sbom = json.loads((ROOT / "docs/sbom/pitwall-sbom.cdx.json").read_text(encoding="utf-8"))
    assert sbom["metadata"]["component"]["version"] == version
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked = {(p["name"], p["version"]) for p in lock["package"] if "version" in p}
    for component in sbom["components"]:
        assert (component["name"], component["version"]) in locked, component["name"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/legal -p no:randomly -q | tail -8`
Expected: three failures (one MIT notice in NOTICE, OpenAI missing, SBOM version `0.1.0a2`).

- [ ] **Step 3: Obtain the exact upstream notice**

Run in an empty scratch directory:
```bash
mkdir -p /tmp/omniroute && cd /tmp/omniroute
npm pack omniroute@3.8.50 --ignore-scripts
sha256sum omniroute-3.8.50.tgz
tar -xzf omniroute-3.8.50.tgz package/LICENSE && cat package/LICENSE
```
Expected: the SHA-256 equals `tarball_sha256` in `config/gateway-catalog.lock.json`. If it does
not match, stop. The catalog was extracted from different bytes, and the lock's `source` fields
name the exact artifact to fetch instead. `package/LICENSE` is an MIT license.

- [ ] **Step 4: Rewrite NOTICE**

Replace the RunPod sentence on line 4 with:

```text
Pitwall is an independent open-source project. It is not affiliated with, endorsed by, sponsored
by, or approved by RunPod, Vast.ai, Together AI, Lambda, OpenAI, Anthropic, Google, GitHub,
Microsoft, xAI, Moonshot AI, Alibaba Cloud, Zhipu AI, MiniMax, DeepSeek, Meta, Hugging Face,
OmniRoute, or any other provider or tool it names. Product and company names are trademarks of
their respective owners and are used only to identify the services and tools Pitwall interoperates
with.
```

Replace the last paragraph (the OmniRoute lines, currently NOTICE lines 38-41) with:

```text
The gateway catalog (`config/gateway-catalog.json` and the seed files generated from it) is
derived from data published in OmniRoute 3.8.50 (npm tarball SHA-256 <tarball_sha256>, pinned in
`config/gateway-catalog.lock.json`). OmniRoute is distributed under the following license, which
applies to that derived material:

    <verbatim package/LICENSE from Step 3, indented four spaces>
```

Keep the existing Agent Routing MIT block, the Paramiko, certifi, and tqdm paragraph, and the
workbench paragraph. Collect the provider list from the README architecture and support matrix
names: `grep -oE "RunPod|Vast\.ai|Together|Lambda|OpenAI|Anthropic|Google|GitHub|xAI|Moonshot|Alibaba|Zhipu|MiniMax|DeepSeek|Meta|Hugging Face" README.md docs/support-matrix.md | sort -u`.
Every name printed must appear in the NOTICE sentence.

- [ ] **Step 5: Regenerate the SBOM**

Run exactly the documented command (`docs/sbom/pitwall-sbom.md:7-8`):
```bash
uv export --frozen --preview-features sbom-export --format cyclonedx1.5 \
  --no-dev --no-emit-project --output-file docs/sbom/pitwall-sbom.cdx.json
```
Then check: `uv run python -c "import json;d=json.load(open('docs/sbom/pitwall-sbom.cdx.json'));print(d['metadata']['component']['version'])"`
→ `0.3.0a1`. If the export prints `0.3.0a1` for the root but `--no-emit-project` drops the
metadata component, drop that flag and keep the root component, so the test's version check has a
subject.
In `pitwall-sbom.md`, add: "Regenerate whenever `uv.lock` or the version changes;
`tests/legal/test_notice_and_sbom.py` fails when the snapshot drifts."

- [ ] **Step 6: Update the legal records**

`docs/legal/gateway-attribution-review.md`: add an "Amendment, 0.3.0a1" block at the top. It
states that the vendored upstream `LICENSE` left with `packages/gateway`, so the full MIT
permission notice is now reproduced in `NOTICE`, which ships in the wheel, sdist, and images. It
replaces the sentence "The MIT notice chain is preserved by the upstream `LICENSE` file shipped
with OmniRoute and is referenced in `NOTICE`" (line 19). Remove "final composition happens in
Task 18 ...", which refers to a lane that no longer exists.

`docs/legal/source-provenance-review.md`: add "Inventory refresh, 0.3.0a1 (2026-10-07)" rows to
the Inventory table:

| Category | Locations | Basis |
| --- | --- | --- |
| Third-party-derived data | `config/gateway-catalog*.json`, `seed/gateway-*.yaml` | OmniRoute 3.8.50 data, MIT; notice in `NOTICE` |
| Compiled extensions | `src/pitwall/workbench/pi_extensions/*.js` | Compiled by TypeScript `$(cat src/pitwall/workbench/pi_extensions/COMPILER)` from the committed `.ts` sources; `make pi-extensions-check` proves equivalence |
| Research material | `docs/research/**` (46 files) | Original notes and model dossiers. Facts and short quotations are attributed to their linked public sources. No third-party documents are copied |
| Binary test fixture | `tests/workbench/fixtures/colors.png` | Generated for the workbench baseline test (`tests/workbench/test_baseline_runner.py:21`); no third-party image |

Correct "No binary media ... or vendored third-party source" to name the PNG fixture and the
OmniRoute-derived data. Before writing the research row, confirm it with
`git ls-files docs/research | xargs grep -lE "^>|Copyright|All rights reserved" | head`. Any file
it prints gets checked, and long verbatim third-party passages get cut down to attributed
summaries in the same commit. Before writing the PNG row, confirm its origin with
`git log --follow --format='%h %s' -- tests/workbench/fixtures/colors.png` and the generator in
the workbench tests, and write the origin you find into the row.

Create `docs/legal/container-image-licenses.md`:

```markdown
# Container image license obligations

Each published image (`api`, `reconciler`, `webhook`, `cost-exporter`, `mcp`) is built on
`python:3.14.7-slim@sha256:caaf356f40667c496d405780745b9ac25771c189a51dfcc42430d531ea09f8a2`
(Debian). The reconciler adds `postgresql-client-<major>` from apt.postgresql.org.

- **Python packages:** the base license profile (`tools/security/check_licenses.py`, no extras).
  Images install `uv export --frozen --no-dev` with no extras (`tests/test_dockerfiles.py`).
- **Debian packages:** each release attaches `<service>.spdx.json`, which lists every dpkg package
  and version in that exact image. Their license texts are in each image under
  `/usr/share/doc/<package>/copyright`. Corresponding source for any GPL- or LGPL-licensed Debian
  package is available, for the exact version listed in the SBOM, from the Debian archive
  snapshot service at `https://snapshot.debian.org/package/<source-package>/<version>/`. The
  PostgreSQL client source is at `https://apt.postgresql.org/pub/repos/apt/pool/main/p/`.
  Requests for source can also be made through a GitHub Discussion on this repository, and the
  maintainer will provide it for at least three years after the release that shipped it.
- **Paramiko (LGPL-2.1), certifi (MPL-2.0), tqdm (MPL-2.0 AND MIT):** shipped unmodified with
  their source files and license texts, as recorded in `NOTICE`.
```

Link this file from `docs/sbom/pitwall-sbom.md` and from NOTICE's "Bundled dependencies"
paragraph.

- [ ] **Step 7: Verify the notice in every artifact form**

Run: `uv run pytest tests/legal -p no:randomly -q | tail -3` → pass.
Run: `rm -rf /tmp/n && uv build --out-dir /tmp/n >/dev/null && unzip -p /tmp/n/*.whl '*.dist-info/licenses/NOTICE' | grep -c "Permission is hereby granted" && tar -xOzf /tmp/n/*.tar.gz --wildcards '*/NOTICE' | grep -c "Permission is hereby granted"`
→ `2` then `2`.
Run: `docker build -q -f docker/Dockerfile.api -t pitwall/api:notice . && docker run --rm pitwall/api:notice sh -c 'grep -c "Permission is hereby granted" /usr/local/lib/python3.14/site-packages/pitwall-*.dist-info/licenses/NOTICE'`
→ `2`.
Run: `uv run python tools/ci/check_markdown_links.py | tail -2` → pass.

- [ ] **Step 8: Commit**

```bash
git add NOTICE docs/legal docs/sbom tests/legal
git commit -s -m "fix(legal): full OmniRoute MIT notice, current SBOM and provenance, image obligations"
```

---

### Task 7: Remove internal operational context (F8)

**Files:**
- Modify: `docs/evidence/2026-09-26-broker-agent-gaps-live.md:18`
- Modify: `docs/superpowers/plans/2026-09-26-broker-agent-gaps.md:11,2144,2184,2187,2188`
- Modify: `docs/superpowers/specs/2026-09-02-pitwall-personal-first-design.md:3`
- Modify: `docs/superpowers/plans/2026-09-02-pitwall-personal-first.md` (lines from Step 1)

- [ ] **Step 1: List every occurrence**

Run: `git grep -nIiE "Zlibkit|vo-rag|track-form|pre-approved|approved in conversation|in conversation on|the user (said|asked|approved|pre-approved)|ListAgents|session [0-9]{2}:[0-9]{2}" -- . ':!docs/superpowers/plans/2026-10-07-public-release-review-fixes.md'`
Record the file:line list. It must include the four files above.

- [ ] **Step 2: Rewrite each line neutrally**

- Evidence line 18: "Clients holding a broker connection were notified before the roll."
- Plan line 11: "**Design:** approved by the maintainer on 2026-09-26. Budget-limit changes during
  execution go through the audited `pitwall_budget_set` surface. Evidence: two `budget_rejected`
  results without a reason after a passing preview, a lost broker connection on a cap-change
  restart, a missing SSH address, and `volume_file_not_configured` from `pitwall_pod_logs`. A
  separate request asked for an SSH address and a 240-minute TTL."
  Keep the live-state sentence (budget figures are not private).
- Plan lines 2144, 2184, 2187, 2188: replace named clients and agent sessions with "connected
  clients" and "a running raw pod". Drop "all requests are pre-approved". Keep historical commit
  hashes and label them "(historical, private repository)" on first mention in each file.
- Spec line 3 and the personal-first plan: "Status: approved by the maintainer on 2026-09-02".

- [ ] **Step 3: Verify**

Re-run Step 1's grep → no output.
Run: `git ls-files -z | xargs -0 env PITWALL_TEXT_POLICY_EXTRA=tools/guards/repo_text_policy.local uv run python tools/guards/repo_text_policy.py; echo $?`
→ `0` (run after Task 8 Step 4 narrows the overlay; before that, `CODE_OF_CONDUCT.md` is the only
expected hit).

- [ ] **Step 4: Commit**

```bash
git add docs/evidence docs/superpowers/plans/2026-09-26-broker-agent-gaps.md docs/superpowers/plans/2026-09-02-pitwall-personal-first.md docs/superpowers/specs/2026-09-02-pitwall-personal-first-design.md
git commit -s -m "docs: replace internal project and session context with neutral descriptions"
```

---

### Task 8: Release runbooks, GHCR steps, and contribution/support policies (F15, F16, F18, F5 in SECURITY.md)

**Files:**
- Modify: `RELEASING.md:20-45` (tag example, public-root CI gate, GHCR visibility, verification)
- Modify: `docs/release/external-release-gates.md:28-40` (remove "Agent Routing prerelease")
- Modify: `docs/release/repository-settings-checklist.md:9-30`
- Modify: `SECURITY.md:5-10` (supported versions), the unrestricted sentence in "Pitwall Agent Routing"
- Modify: `CONTRIBUTING.md:185-190`
- Modify: `CODE_OF_CONDUCT.md:61-66`
- Modify (untracked, local): `tools/guards/repo_text_policy.local:2`
- Create: `tests/docs/test_release_runbooks.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/docs/test_release_runbooks.py
"""Release runbooks match the one-package v* workflow and the public-root process."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_runbooks_use_the_current_version_and_no_component_tags() -> None:
    version = tomllib.loads(_read("pyproject.toml"))["project"]["version"]
    releasing = _read("RELEASING.md")
    assert f"git tag -s v{version}" in releasing
    assert "git tag -v" in releasing
    assert "0.1.0a2" not in releasing
    for relative in ("docs/release/external-release-gates.md", "docs/release/repository-settings-checklist.md"):
        text = _read(relative)
        assert "agent-routing/v" not in text and "gateway/v" not in text, relative


def test_runbook_requires_public_root_ci_and_public_packages() -> None:
    releasing = _read("RELEASING.md")
    assert "git rev-list --count" in releasing
    assert "successful `CI` run for that exact commit" in releasing
    assert "Change visibility" in releasing
    assert "docker logout ghcr.io" in releasing


def test_tag_signatures_are_not_described_as_a_ruleset_check() -> None:
    checklist = _read("docs/release/repository-settings-checklist.md")
    assert "requires signatures" not in checklist


def test_policies_name_dco_supported_version_and_conduct_mailbox() -> None:
    assert "https://developercertificate.org/" in _read("CONTRIBUTING.md")
    assert "signed off" in _read("CONTRIBUTING.md")
    assert "`v0.3.0a1`" in _read("SECURITY.md")
    assert "chris@lateapexllc.com" in _read("CODE_OF_CONDUCT.md")
    assert "PITWALL_AGENTS_UNRESTRICTED=1` setting bypasses" not in _read("SECURITY.md")
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/docs/test_release_runbooks.py -p no:randomly -q | tail -8` → 4 failures.

- [ ] **Step 3: Rewrite the runbooks**

`RELEASING.md` "Before tagging". Replace step 5 and add steps:

```markdown
5. Confirm the public repository's history is the single approved root:
   `git rev-list --count HEAD` prints `1` and `git cat-file -p HEAD` has no `parent` line.
6. Confirm a successful `CI` run for that exact commit on the public repository:
   `gh run list --repo Buckeyes22/pitwall --workflow ci.yml --commit "$(git rev-parse HEAD)" --status success --json databaseId --jq length`
   prints at least `1`. CI results from any other repository or commit do not count;
   `release-readiness.yml` enforces the same check against `GITHUB_SHA`.
7. Create and verify a signed annotated tag, then push it:

   ```bash
   git tag -s v0.3.0a1 -m "Pitwall 0.3.0a1"
   git tag -v v0.3.0a1
   git push origin v0.3.0a1
   ```

   The tag signature is verified locally with `git tag -v`. GitHub's tag ruleset controls who may
   create, update, or delete `v*` tags; its "Require signed commits" rule checks commit
   signatures, not tag objects.
```

Add a section "After the workflow publishes" with these items:
- First publication only: for each of `api`, `reconciler`, `webhook`, `cost-exporter`, and
  `mcp`, open the package `ghcr.io/buckeyes22/pitwall/<service>`. In **Package settings**, confirm
  "Inherit access from repository" names `Buckeyes22/pitwall`. Then under **Danger Zone →
  Change visibility**, select **Public**. Public is irreversible, and new packages start private
  even from a public repository. Leave `pitwall-staging/*` private.
- Anonymous pulls: `docker logout ghcr.io` and then, for each service,
  `docker pull "$(cat <service>.digest)"` using the downloaded digest files. Every pull succeeds.
- Assets: `gh release download v0.3.0a1 --repo Buckeyes22/pitwall -D /tmp/v0.3.0a1 && cd /tmp/v0.3.0a1 && sha256sum -c SHA256SUMS`
  prints `OK` for every line. Then run `gh attestation verify pitwall-0.3.0a1-py3-none-any.whl --repo Buckeyes22/pitwall`.
  For each image, run `gh attestation verify oci://"$(cat api.digest)" --repo Buckeyes22/pitwall`.
- Install: in a clean `python:3.14.7-slim` container, run the release-wheel install string, then
  `pitwall --version` → `0.3.0a1`.

`docs/release/external-release-gates.md`: delete the "Agent Routing prerelease" section (lines
28-40 and following items). Releases are only `v*` tags of the one package (`SECURITY.md:9`
already says so).

`docs/release/repository-settings-checklist.md`:
- "Release tags" row: "Tag ruleset on `refs/tags/v*` restricts creation, update, and deletion to
  the maintainer and blocks non-fast-forward updates. Tag signatures are verified with
  `git tag -v` (see `RELEASING.md`), because rulesets do not check tag-object signatures."
- "Releases" row: drop "Agent Routing and Gateway publish directly from their namespaced tag workflows".
- "Packages" row: add "production packages made Public after first publish; staging stays private".
- Delete the "Agent Routing release controls" section.

- [ ] **Step 4: Policies**

`SECURITY.md` Supported Versions table:

```markdown
| Default branch (`main`) | :white_check_mark: | Security fixes are made here |
| `v0.3.0a1` (2026-10-07) | :white_check_mark: | Current alpha. Fixes ship in the next alpha; no backports to older alphas. |
| Earlier alphas (`v0.1.0a1`–`v0.2.0a1`) | :x: | Upgrade to the current alpha. |
```

Keep the component-tags row. Replace the "default `PITWALL_AGENTS_UNRESTRICTED=1` profile"
sentence with: "By default each child CLI keeps its own sandbox and approval policy. Setting
`PITWALL_AGENTS_UNRESTRICTED=1` permits unattended child execution and bypasses those prompts."

`CONTRIBUTING.md` item 5:

```markdown
5. **DCO sign-off.** Every commit must be signed off. Add `-s` to the commit command:
   ```bash
   git commit -s -m "fix: handle nil pointer in lease state"
   ```
   The `Signed-off-by:` line certifies the [Developer Certificate of Origin 1.1](https://developercertificate.org/)
   for that commit. A sign-off is a statement, not a cryptographic signature; GPG or SSH commit
   signing is optional.
```

`CODE_OF_CONDUCT.md` Enforcement paragraph:

```markdown
Instances of abusive, harassing, or otherwise unacceptable behavior may be reported confidentially
to chris@lateapexllc.com. Only the project maintainer reads this mailbox. Do not include the
report in a public issue or discussion.
```

Local overlay: narrow the private-name rule on line 2 of `tools/guards/repo_text_policy.local` so
it allows the DCO sign-off address. This file is untracked. Record the change in the PR description so
the next workstation copy matches.

- [ ] **Step 5: Verify the mailbox delivers**

Send a test message to `chris@lateapexllc.com` from an outside account. Expected: it arrives in
the maintainer's inbox. Task 13 does not start until it does.

- [ ] **Step 6: Verify**

Run: `uv run pytest tests/docs/test_release_runbooks.py -p no:randomly -q | tail -3` → pass.
Run: `uv run python tools/ci/check_markdown_links.py | tail -2` → pass.
Run: `git ls-files -z | xargs -0 env PITWALL_TEXT_POLICY_EXTRA=tools/guards/repo_text_policy.local uv run python tools/guards/repo_text_policy.py; echo $?` → `0`.

- [ ] **Step 7: Commit**

```bash
git add RELEASING.md docs/release SECURITY.md CONTRIBUTING.md CODE_OF_CONDUCT.md tests/docs/test_release_runbooks.py
git commit -s -m "docs(release): public-root runbook, GHCR visibility, DCO 1.1, supported versions, conduct mailbox"
```

---

### Task 9: README newcomer path (F1, F3, F5, F6, F7, F13, F17)

**Files:**
- Modify: `README.md:7-9` (opening claim), `:45-88` (examples), `:133-228` (Quick Start), `:378-387` (Legal)
- Create: `examples/prompts/first-dispatch.md`
- Create: `tests/docs/test_readme_newcomer.py`

**Interfaces:**
- Consumes: the release-wheel install string (Global Constraints), the Task 2 default, and the
  install-reference validator (Task 1).

- [ ] **Step 1: Write the failing test**

```python
# tests/docs/test_readme_newcomer.py
"""The README path works for someone with no prior Pitwall state."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
README = (ROOT / "README.md").read_text(encoding="utf-8")
BLOCKS = re.findall(r"```bash\n(.*?)```", README, flags=re.S)


def test_install_uses_the_release_wheel() -> None:
    assert (
        "uv tool install --python 3.14.7 https://github.com/Buckeyes22/pitwall/releases/download/"
        "v0.3.0a1/pitwall-0.3.0a1-py3-none-any.whl"
    ) in README


def test_every_broker_terminal_loads_the_same_configuration() -> None:
    needs_config = [b for b in BLOCKS if "pitwall-api" in b or "pitwall db migrate" in b]
    assert len(needs_config) >= 2
    for block in needs_config:
        assert "cd pitwall" in block
        assert "set -a; . ./.env.quickstart.local; set +a" in block


def test_dispatch_examples_run_from_path_with_a_shipped_prompt() -> None:
    assert "pitwall agents dispatch codex examples/prompts/first-dispatch.md" in README
    assert "codex-shim.sh prompt.md" not in README
    assert (ROOT / "examples/prompts/first-dispatch.md").is_file()


def test_disclosures_precede_the_first_dispatch_and_serve() -> None:
    first_dispatch = README.index("pitwall agents dispatch")
    first_serve = README.index("pitwall serve")
    assert README.index("PITWALL_AGENTS_UNRESTRICTED=1") < first_dispatch
    assert README.index("## Where your data goes") < first_dispatch
    assert README.index("RUNPOD_API_KEY") < first_serve
    assert "before any paid call goes out" not in README


def test_non_affiliation_covers_named_providers() -> None:
    legal = README.split("### Trademark and Non-Affiliation", 1)[1]
    for name in ("RunPod", "OpenAI", "Anthropic", "Google", "GitHub"):
        assert name in legal
    assert "interoperab" in legal
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/docs/test_readme_newcomer.py -p no:randomly -q | tail -8` → 5 failures.

- [ ] **Step 3: Opening claim (F7)**

Replace README lines 6-9 ("It estimates what a request will cost ... before any paid call goes
out.") with:

```markdown
Pitwall is one broker for the GPU and inference spend of AI-assisted development. For requests it
brokers, it estimates the cost and checks it against a monthly budget and a per-request cap before
the provider call. It routes inference to a provider that can serve it. It also routes your coding
work to the model and agent harness that suits the task, and tracks what all of it costs.

These limits are Pitwall's own admission checks, not a spending ceiling on your provider accounts.
Coding harnesses bill their own subscriptions or API keys directly. Personal `pitwall serve` keeps a
separate local ledger. Automatic kill-switch escalation on a budget breach is off by default
(`PITWALL_BUDGET_BREACH_KILL_MODE=disabled`; see the Configuration section).
```

- [ ] **Step 4: Disclosures before the examples (F5, F17)**

Insert directly after the five-part bullet list and before `## What you can do with it` (line 35):

```markdown
### Before you dispatch

Agent Routing runs the harness CLIs you have installed, as you, with your files, credentials, and
network. By default each CLI keeps its own sandbox and approval prompts. For unattended runs,
`export PITWALL_AGENTS_UNRESTRICTED=1` passes each CLI's bypass flag (for Codex,
`--dangerously-bypass-approvals-and-sandbox`), so the child can run commands without asking. Kimi
Code and dsh dispatch only with that setting. See Agent Routing security (`docs/agents/SECURITY.md`).

## Where your data goes

- **Model providers.** Prompts, and any code or files a harness reads, go to the provider behind
  that harness or route (OpenAI, Anthropic, Google, a RunPod pod you serve, and so on) under that
  provider's terms.
- **Local run records.** Each dispatch's stdout and stderr are kept under
  `~/.local/state/pitwall/agents/runs/` (mode 0600), with no automatic deletion. Remove them with
  `pitwall agents runs cleanup --older-than <days>`. Prompt bodies are stored only with
  `--routing-retain-prompt`.
- **Process listings.** Kimi Code, Antigravity, Qwen Code, Hermes, Cline, ZCode, and dsh receive
  the prompt as a command-line argument, so other processes of the same user (`ps`) can see it
  while the child runs.
- **Optional destinations, off unless configured:** Langfuse tracing (when
  `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are set; without `LANGFUSE_HOST` it sends to
  `https://cloud.langfuse.com`), Resend email notifications, Cloudflare R2 storage, and outbound
  webhooks you subscribe.
```

Check the Langfuse variable names against `src/pitwall/config.py` (`langfuse_public_key`,
`langfuse_secret_key`, `langfuse_host` and their aliases near line 599). Check the R2, Resend, and
webhook setting names the same way, and use the exact environment names in the text.

- [ ] **Step 5: Examples and Quick Start (F1, F6, F7)**

In "Send a prompt to another model" (lines 64-73), replace the code block with:

```bash
pitwall agents dispatch codex examples/prompts/first-dispatch.md
pitwall agents dispatch route ornith examples/prompts/first-dispatch.md
```

Then add after it: "`pitwall agents install` also writes `codex-shim.sh` and the other shims to
`~/.claude/scripts/`, which is not on `PATH`. Coding-agent hosts call them by full path. To run
them yourself, `export PATH="$HOME/.claude/scripts:$PATH"`."

Create `examples/prompts/first-dispatch.md`:

```markdown
List the files in the current directory and summarize what this project does in three sentences.
Do not modify any files.
```

Quick Start install block (line 139): use the release-wheel install string. The checkout
alternative becomes
`git clone --branch v0.3.0a1 https://github.com/Buckeyes22/pitwall.git && cd pitwall && uv tool install --python 3.14.7 .`

"Route work to other models": after `pitwall agents install`, add the harness sequence for Codex:

```bash
pitwall agents setup harnesses      # installs missing harness CLIs after you confirm; never logs in
codex login                         # authenticate Codex yourself
pitwall agents doctor --harness codex --live-auth
pitwall agents dispatch codex examples/prompts/first-dispatch.md
```

Before writing it, confirm with `pitwall agents setup harnesses --dry-run` that the command
offers Codex. Confirm with `pitwall agents doctor --harness codex --live-auth --json` that a
logged-in Codex reports PASS. Use the exact readiness text that doctor prints.

"Serve a model on a RunPod pod" (lines 165-176): before the serve pointer, add:

```markdown
You need a RunPod account with billing set up and an API key in `RUNPOD_API_KEY`
(`pitwall setup` checks for it). RunPod starts charging when the pod is created, before the model
answers, and charges until the pod is terminated. `pitwall stop <route>` terminates it. The pod
also carries its own deadline from `--ttl-minutes`, and RunPod treats that deadline as best effort.
If `pitwall status` or the RunPod console still shows a pod you did not expect, follow
orphan cleanup (`docs/operator/troubleshooting.md`).
```

- [ ] **Step 6: Broker quick start configuration (F3)**

Replace the "Run the broker locally" code blocks (lines 184-198 and 210-212) with:

```bash
git clone --branch v0.3.0a1 https://github.com/Buckeyes22/pitwall.git
cd pitwall
uv sync --frozen --extra dev --python 3.14.7
docker compose -f docker-compose.testinfra.yml up -d --wait

cat > .env.quickstart.local <<'EOF'
DATABASE_URL=postgresql://pitwall:pitwall@127.0.0.1:5444/pitwall_test
REDIS_URL=redis://127.0.0.1:6380/0
RUNPOD_API_KEY=local-dry-run-key
PITWALL_ADMIN_SECRET=local-admin-secret
PITWALL_API_TOKEN=local-api-token
EOF
set -a; . ./.env.quickstart.local; set +a

uv run pitwall db migrate
uv run pitwall init --non-interactive
```

"In a second terminal, start the API from the same checkout. It loads the same file":

```bash
cd pitwall
set -a; . ./.env.quickstart.local; set +a
uv run pitwall-api
```

State that `.env.quickstart.local` holds fake placeholder values and is ignored by Git
(`.gitignore:78` `.env.*.local`). Remove "Run it from the same checkout", which the blocks now
show.

- [ ] **Step 7: Legal (F13)**

Replace the "Trademark and Non-Affiliation" paragraph with:

```markdown
Pitwall is an independent open-source project. It is not affiliated with, endorsed by, sponsored
by, or approved by RunPod, Vast.ai, Together AI, Lambda, OpenAI, Anthropic, Google, GitHub, or any
other provider, model vendor, or tool named in this repository. Those names are trademarks of their
respective owners and are used only as interoperability references, to identify the services and
CLIs Pitwall works with. The Apache-2.0 license grants no rights to any third-party trademark. See
`NOTICE`.
```

- [ ] **Step 8: Verify, including a literal fresh run**

Run: `uv run pytest tests/docs/test_readme_newcomer.py tests/release/test_install_references.py tests/test_markdown_links.py -p no:randomly -q | tail -5`
→ all pass. This includes `test_tracked_tree_has_no_bare_install_and_current_wheel_urls`, now
that README is fixed.

Fresh-environment run of the broker path. Before release, the wheel URL does not exist yet, so
use a clone of this branch and the locally built wheel:
```bash
d=$(mktemp -d) && git clone -q --branch fix/public-release-review "$PWD" "$d/pitwall" && cd "$d"
# then paste the README's "Run the broker locally" blocks, replacing only the clone line
```
Expected: `pitwall db migrate` applies migrations, and `pitwall init` prints the smoke command.
In a second shell, follow the second block exactly; `pitwall-api` starts. The README's `curl`
returns JSON containing `"dry_run":true`. Afterwards, run
`docker compose -f docker-compose.testinfra.yml down` in `$d/pitwall`. (Task 11 serializes this
with other DB suites. It uses the shared `pitwall_test` database.)

- [ ] **Step 9: Commit**

```bash
git add README.md examples/prompts/first-dispatch.md tests/docs/test_readme_newcomer.py
git commit -s -m "docs(readme): release-wheel install, working broker quick start, execution, spend, and data disclosures"
```

---

### Task 10: Dependabot version updates (F19)

**Files:**
- Create: `.github/dependabot.yml`
- Modify: `docker/Dockerfile.{api,cost-exporter,mcp,reconciler,webhook}:1-2,17` (literal `FROM`)
- Modify: `tests/test_dockerfiles.py:31-32` (`_stages`)
- Create: `tests/release/test_dependabot.py`

The Dockerfiles select the base image through `ARG PYTHON_IMAGE`, which no build overrides
(`git grep -n PYTHON_IMAGE` finds only the Dockerfiles, their test, and an old plan). Dependabot's
docker updater reads `FROM` lines, so the image reference becomes literal.

- [ ] **Step 1: Write the failing test**

```python
# tests/release/test_dependabot.py
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_dependabot_covers_every_pinned_ecosystem() -> None:
    config = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text(encoding="utf-8"))
    assert config["version"] == 2
    seen = {(u["package-ecosystem"], u["directory"]) for u in config["updates"]}
    assert seen == {
        ("uv", "/"),
        ("github-actions", "/"),
        ("docker", "/docker"),
        ("docker-compose", "/"),
        ("npm", "/tools/pi-deps"),
    }
    for update in config["updates"]:
        assert update["groups"], update["package-ecosystem"]
```

If PyYAML is not in the dev environment (`uv run python -c "import yaml"`), parse with the same
YAML loader `tests/release/test_workflows.py` uses (`_load`).

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/release/test_dependabot.py -q | tail -3` → `FileNotFoundError`.

- [ ] **Step 3: Implement**

```yaml
# .github/dependabot.yml
version: 2
updates:
  - package-ecosystem: "uv"
    directory: "/"
    schedule:
      interval: "weekly"
    groups:
      python-minor-and-patch:
        update-types: ["minor", "patch"]
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
    groups:
      actions:
        patterns: ["*"]
  - package-ecosystem: "docker"
    directory: "/docker"
    schedule:
      interval: "weekly"
    groups:
      base-images:
        patterns: ["*"]
  - package-ecosystem: "docker-compose"
    directory: "/"
    schedule:
      interval: "weekly"
    groups:
      compose-images:
        patterns: ["*"]
  - package-ecosystem: "npm"
    directory: "/tools/pi-deps"
    schedule:
      interval: "weekly"
    groups:
      pi:
        patterns: ["*"]
```

Every Dockerfile: delete line 1 (`ARG PYTHON_IMAGE=...`). Replace both `FROM $PYTHON_IMAGE AS builder`
and `FROM $PYTHON_IMAGE AS runtime` with
`FROM python:3.14.7-slim@sha256:caaf356f40667c496d405780745b9ac25771c189a51dfcc42430d531ea09f8a2 AS <stage>`.

`tests/test_dockerfiles.py::_stages`:

```python
    parts = re.split(
        r"^FROM python:3\.14\.7-slim@sha256:[0-9a-f]{64} AS (\w+)\n", text, flags=re.M
    )
    assert parts[0].strip() == ""
```

`docs/legal/container-image-licenses.md` (Task 6) names the same digest. A Dependabot base-image
bump must update that file too. Add to the test:

```python
def test_container_license_doc_names_the_current_base_digest() -> None:
    dockerfile = (ROOT / "docker" / "Dockerfile.api").read_text(encoding="utf-8")
    digest = re.search(r"sha256:[0-9a-f]{64}", dockerfile).group(0)
    assert digest in (ROOT / "docs/legal/container-image-licenses.md").read_text(encoding="utf-8")
```

The npm Dependabot group bumps `tools/pi-deps`. `tests/workbench/test_pi_deps_lock.py` then fails
until `harness-installers.json` moves with it. That is the intended coupling.

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/release/test_dependabot.py tests/test_dockerfiles.py tests/release_acceptance/test_ops_inventory.py -p no:randomly -q | tail -5` → pass.
If `test_ops_inventory.py` asserts on the `ARG PYTHON_IMAGE` form for the real Dockerfiles (not
its synthetic fixture at lines 53-54), update the inventory tool it exercises to accept a literal
`FROM` and keep the fixture case. The code that fails names the tool.
Run: `docker build -q -f docker/Dockerfile.api -t pitwall/api:literal-from . && docker run --rm pitwall/api:literal-from pitwall --version` → `0.3.0a1`.

- [ ] **Step 5: Commit**

```bash
git add .github/dependabot.yml docker tests/test_dockerfiles.py tests/release/test_dependabot.py tests/release_acceptance
git commit -s -m "chore(deps): Dependabot version updates for uv, Actions, Docker, Compose, and npm"
```

---

### Task 11: Integrate and prove the branch (all findings)

**Files:**
- Modify: `CHANGELOG.md` (`## [0.3.0a1]` section: add the user-visible changes from Tasks 1–10)
- Modify: `docs/releases/v0.3.0a1.md` (install and verification section, restricted default note)

- [ ] **Step 1: Merge lanes**

If lanes ran, merge `L1`…`L5` into `fix/public-release-review` in the order L2, L3, L4, L1, L5.
L5 last, because the README test depends on L1's validator. Each lane's report must end with
`STATUS: DONE` and pasted validation output. Check every claim against the repository
(`git diff`, rerun its validation), not against the report.

- [ ] **Step 2: Changelog and release notes**

Under `## [0.3.0a1]` in `CHANGELOG.md`, add a "Changed" entry, "Harness CLIs keep their own
sandbox and approval prompts unless `PITWALL_AGENTS_UNRESTRICTED=1` is set (previously unset meant
unrestricted)". Add "Fixed" entries for the install URL, the broker quick start, checksums,
notice, SBOM, and the license inventory. In `docs/releases/v0.3.0a1.md`, the "Installation and
verification" section uses the release-wheel install string and `sha256sum -c SHA256SUMS`, and has
a "Behavior change" paragraph for the restricted default.

- [ ] **Step 3: CI parity, run once**

Run each command and keep its last lines:
```bash
uv sync --frozen --all-extras --python 3.14.7
uv run ruff check . && uv run ruff format --check .            # whole tree
uv run mypy src                                                # as ci.yml lint job runs it
make test-fast | tail -40
make up && make test-int | tail -40                             # alone; shared pitwall_test
scripts/release/run-user-journeys.sh 2>&1 | tail -40            # alone, after test-int
make license-check >/dev/null && echo license=ok
make pi-extensions-check
make docs-check | tail -3
uvx --from semgrep==1.165.0 semgrep scan --error --config p/python --config p/security-audit \
  --exclude-rule python.lang.security.insecure-hash-algorithms-md5.insecure-hash-algorithm-md5 \
  --config tools/security/semgrep.yml src/pitwall
for f in docker/Dockerfile.*; do docker build -q -f "$f" . >/dev/null || echo "FAIL $f"; done
uv run python scripts/release/validate_candidate.py v0.3.0a1 --allow-dirty 2>&1 | tail -5
git ls-files -z | xargs -0 env PITWALL_TEXT_POLICY_EXTRA=tools/guards/repo_text_policy.local uv run python tools/guards/repo_text_policy.py; echo text=$?
git log --format='%(trailers:key=Signed-off-by,valueonly)' feat/zcode-harness..HEAD | grep -c .
```
Expected: ruff, mypy, and every suite pass. `license=ok`. The extension check matches. Links
pass. Semgrep reports no findings. No `FAIL` lines. The validator prints no errors. `text=0`.
The sign-off count equals `git rev-list --count feat/zcode-harness..HEAD`. Use the exact lint,
mypy, and pip-audit invocations from `.github/workflows/ci.yml` if they differ from the lines
above, and confirm `validate_candidate.py`'s argument form with `--help`. Any failure gets fixed
at its root, then rerun.

- [ ] **Step 4: README run in a fresh environment (day-one item)**

In a clean `python:3.14.7-slim` container with Docker access, or in a new VM user with no
`~/.config/pitwall`, `~/.local/state/pitwall`, or `~/.claude`, follow `README.md` Quick Start
literally. Substitute the wheel only: `uv build --wheel` on this branch, then
`uv tool install --python 3.14.7 dist/pitwall-0.3.0a1-py3-none-any.whl`. Expected:
`pitwall --version` prints `0.3.0a1`. `pitwall agents install` writes the shims.
`pitwall agents doctor` reports `security.unrestricted_mode` PASS. The broker path ends with
`"dry_run":true`.

- [ ] **Step 5: One PR into private main**

```bash
git push -u origin fix/public-release-review
gh pr create --repo Buckeyes22/pitwall --base main --title "Public release review fixes (0.3.0a1)" --body-file /tmp/pr-body.md
```
The body lists findings 1–19 with their commits and the local overlay change from Task 8. Wait
once for CI with `gh pr checks --watch --fail-fast`. Merge when `CI` is green.

---

### Task 12: Export the single approved root commit (F2)

**Files:** none in this repository. The output is `~/git/pitwall-public`.

- [ ] **Step 1: Build the root commit from the merged tree**

```bash
cd ~/git/pitwall && git fetch origin && tree=$(git rev-parse origin/main^{tree})
msg=$'Pitwall 0.3.0a1\n\nSigned-off-by: chris <chris@lateapexllc.com>'
root=$(GIT_AUTHOR_NAME=chris GIT_AUTHOR_EMAIL=chris@lateapexllc.com \
       GIT_COMMITTER_NAME=chris GIT_COMMITTER_EMAIL=chris@lateapexllc.com \
       git commit-tree "$tree" -m "$msg")
echo "$root"
```

- [ ] **Step 2: Replace the stale staging repository's history**

`~/git/pitwall-public` currently holds commit `6f0a339` with tree `dd1f5719`, which is not the
approved tree. Rebuild it:
```bash
rm -rf ~/git/pitwall-public && git init -q -b main ~/git/pitwall-public
git -C ~/git/pitwall update-ref refs/export/public-root "$root"
git -C ~/git/pitwall-public fetch -q ~/git/pitwall refs/export/public-root:refs/heads/main
git -C ~/git/pitwall-public checkout -q main
git -C ~/git/pitwall update-ref -d refs/export/public-root
```

- [ ] **Step 3: Verify**

```bash
cd ~/git/pitwall-public
git rev-list --count --all                      # 1
git cat-file -p HEAD | grep -c '^parent'        # 0
test "$(git rev-parse HEAD^{tree})" = "$(git -C ~/git/pitwall rev-parse origin/main^{tree})" && echo tree=match
git log --format='%an <%ae> | %cn <%ce>'        # chris <chris@lateapexllc.com> | chris <chris@lateapexllc.com>
git tag | wc -l                                 # 0
git for-each-ref --format='%(refname)'          # refs/heads/main only
git ls-files -z | xargs -0 env PITWALL_TEXT_POLICY_EXTRA=$HOME/git/pitwall/tools/guards/repo_text_policy.local \
  ~/git/pitwall/.venv/bin/python ~/git/pitwall/tools/guards/repo_text_policy.py; echo text=$?   # text=0
```
All of these must hold before Task 13.

---

### Task 13: Public repository and settings (F4, F16, day-one)

Outward-facing. Show these steps to the maintainer as one batch and get one approval, then run
them in order.

- [ ] **Step 1: Free the name**

```bash
gh repo rename pitwall-private --repo Buckeyes22/pitwall --yes
gh repo view Buckeyes22/pitwall-private --json visibility --jq .visibility   # PRIVATE
git -C ~/git/pitwall remote set-url origin https://github.com/Buckeyes22/pitwall-private.git
```

- [ ] **Step 2: GHCR namespaces**

```bash
gh auth refresh -h github.com -s read:packages,write:packages,delete:packages
gh api "/users/Buckeyes22/packages?package_type=container" --jq '.[] | [.name, .visibility, (.repository.full_name // "-")] | @tsv'
```
Any existing `pitwall/<service>` or `pitwall-staging/<service>` package linked to
`Buckeyes22/pitwall-private` would deny the new repository's `GITHUB_TOKEN` write access. For each
one, open its **Package settings → Manage Actions access** and add `Buckeyes22/pitwall` with the
**Write** role. If the private repository's images must not appear under the public package, delete
those package versions first, after the maintainer approves:
`gh api -X DELETE /user/packages/container/<url-encoded name>`.
Expected after this step: no package is linked only to `pitwall-private` without `Buckeyes22/pitwall`
write access.

- [ ] **Step 3: Create and push**

```bash
gh repo create Buckeyes22/pitwall --public --description "Budget-gated broker for GPU inference and AI coding-agent routing" --disable-wiki
git -C ~/git/pitwall-public remote add origin https://github.com/Buckeyes22/pitwall.git
git -C ~/git/pitwall-public push -u origin main
gh api repos/Buckeyes22/pitwall/commits/main --jq '.parents | length'   # 0
```

- [ ] **Step 4: Security and community settings**

```bash
gh api -X PUT repos/Buckeyes22/pitwall/private-vulnerability-reporting
gh api repos/Buckeyes22/pitwall/private-vulnerability-reporting --jq .enabled          # true
gh repo edit Buckeyes22/pitwall --enable-issues --enable-discussions
gh api -X PATCH repos/Buckeyes22/pitwall -F 'security_and_analysis[secret_scanning][status]=enabled' \
  -F 'security_and_analysis[secret_scanning_push_protection][status]=enabled'
gh api -X PUT repos/Buckeyes22/pitwall/vulnerability-alerts
gh api -X PUT repos/Buckeyes22/pitwall/automated-security-fixes
gh api -X PUT repos/Buckeyes22/pitwall/actions/permissions/workflow -f default_workflow_permissions=read -F can_approve_pull_request_reviews=false
gh api repos/Buckeyes22/pitwall --jq '.security_and_analysis'
```
Expected: secret scanning and push protection `enabled`. The dependency graph is on by default for
public repositories. Confirm it under **Insights → Dependency graph**.

Non-maintainer check (F4): open `https://github.com/Buckeyes22/pitwall/security` in a signed-in
browser session of a different GitHub account. Expected: a **Report a vulnerability** button.

- [ ] **Step 5: Rulesets and environments**

Apply `docs/release/repository-settings-checklist.md` as corrected in Task 8:
- Branch ruleset on `main`: require a pull request (0 approvals), require status check `required`
  (the aggregate job in `ci.yml`), block force pushes and deletion.
- Tag ruleset on `refs/tags/v*`: restrict creations, updates, and deletions to repository admins;
  block force pushes.
- Environments `ghcr-staging`, `ghcr`, `github-release`: each with a deployment branch and tag
  policy allowing only tags matching `v*`.

```bash
for e in ghcr-staging ghcr github-release; do
  gh api -X PUT repos/Buckeyes22/pitwall/environments/$e -F 'deployment_branch_policy[protected_branches]=false' -F 'deployment_branch_policy[custom_branch_policies]=true'
  gh api -X POST repos/Buckeyes22/pitwall/environments/$e/deployment-branch-policies -f name='v*' -f type=tag
done
gh api repos/Buckeyes22/pitwall/rulesets --jq '.[] | [.name, .target, .enforcement] | @tsv'
```
Create the two rulesets in **Settings → Rules → Rulesets** (or `gh api -X POST repos/Buckeyes22/pitwall/rulesets --input <json>`).
Then list them. Expected: one branch ruleset and one tag ruleset, both `active`.

---

### Task 14: CI on the exact public root (F2, F15, day-one)

- [ ] **Step 1: Wait for the push-triggered run**

```bash
sha=$(git -C ~/git/pitwall-public rev-parse HEAD)
run=$(gh run list --repo Buckeyes22/pitwall --workflow ci.yml --commit "$sha" --json databaseId --jq '.[0].databaseId')
gh run watch "$run" --repo Buckeyes22/pitwall --exit-status
gh run list --repo Buckeyes22/pitwall --workflow ci.yml --commit "$sha" --status success --json databaseId --jq length
```
Expected: the watch exits 0 and the count is `1`. `ci.yml` runs on `push` to `main`, so Task 13's
push starts this run. If CI fails here, fix the cause in the private repository and repeat Tasks
11–14 with a new root commit. The `main` ruleset blocks force pushes, so delete and recreate the
public repository (Task 13 Steps 3–5) rather than rewriting its `main`.

---

### Task 15: Tag, publish, and verify v0.3.0a1 (F14, F16, day-one)

- [ ] **Step 1: Enable and tag**

```bash
gh variable set PITWALL_RELEASE_ENABLED --repo Buckeyes22/pitwall --body true
cd ~/git/pitwall-public
git tag -s v0.3.0a1 -m "Pitwall 0.3.0a1" && git tag -v v0.3.0a1
git push origin v0.3.0a1
run=$(gh run list --repo Buckeyes22/pitwall --workflow release.yml --json databaseId,headBranch --jq '.[] | select(.headBranch=="v0.3.0a1") | .databaseId' | head -1)
gh run watch "$run" --repo Buckeyes22/pitwall --exit-status
```
Expected: `git tag -v` reports a good signature, and the release run exits 0.

- [ ] **Step 2: Make production images public (first release)**

For each of `api`, `reconciler`, `webhook`, `cost-exporter`, `mcp`: open
`https://github.com/users/Buckeyes22/packages/container/pitwall%2F<service>/settings`. Confirm the
linked repository is `Buckeyes22/pitwall`, then **Change visibility → Public**. This is
irreversible. Leave `pitwall-staging/*` private.

- [ ] **Step 3: Verify everything a stranger downloads**

```bash
rm -rf /tmp/v0.3.0a1 && gh release download v0.3.0a1 --repo Buckeyes22/pitwall -D /tmp/v0.3.0a1
cd /tmp/v0.3.0a1 && sha256sum -c SHA256SUMS
gh attestation verify pitwall-0.3.0a1-py3-none-any.whl --repo Buckeyes22/pitwall
gh attestation verify pitwall-0.3.0a1.tar.gz --repo Buckeyes22/pitwall
docker logout ghcr.io
for s in api reconciler webhook cost-exporter mcp; do
  ref=$(cat "$s.digest"); docker pull -q "$ref" && docker run --rm "$ref" pitwall --version
  gh attestation verify "oci://$ref" --repo Buckeyes22/pitwall >/dev/null && echo "$s attested"
done
docker run --rm python:3.14.7-slim sh -c 'pip install -q uv && uv tool install --python 3.14.7 https://github.com/Buckeyes22/pitwall/releases/download/v0.3.0a1/pitwall-0.3.0a1-py3-none-any.whl >/dev/null && ~/.local/bin/pitwall --version'
```
Expected: two `OK` lines from `sha256sum -c`. Both package attestations verify. Each of the five
anonymous pulls succeeds and prints `0.3.0a1`, followed by `<service> attested`. The clean
container prints `0.3.0a1`.

- [ ] **Step 4: Record**

Add the run URLs and the command output from Tasks 14–15 to the private repository as
`docs/evidence/2026-10-07-public-release-v0.3.0a1.md` (neutral wording, no private context). Then
update the "Repo temporarily private" memory to state that the private history lives at
`Buckeyes22/pitwall-private` and the public root is published.

---

### Task 16: Brittleness audit fixes before the v0.3.0a1 tag (continuation)

The first v0.3.0a1 tag run (37827528877) failed in `readiness / artifacts`: pinned uv 0.11.19 has no
CPython 3.14.7 download. Nothing was published. PR #11 on the public repo (`fix/release-python-pin`,
7fd3510) replaces `uv python install` with `actions/setup-python`. A five-lane read-only audit (A pins,
B tag-only paths, C test contracts, D runtime, E newcomer path) followed. Every finding below is fixed
on `fix/release-python-pin` before the tag is moved. Finding ids: A1 = audit A finding 1, etc.

Global rules for every lane:
- Work only in your worktree `~/git/pw-lane-<id>` on branch `lane/<id>` (based on 7fd3510). Commit with
  `git commit -s`, author `chris <chris@lateapexllc.com>` (the repo config already sets it), plus the trailers
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01XoQt2c6WJXqKq1TuP99ErE`. Never push.
- Python only via `uv run --frozen` (run `uv sync --frozen --extra dev` first). Never bare python.
- Hermetic only: no live credentials, no paid providers. Run every CLI with `HOME=$(mktemp -d)` and
  `XDG_*` unset or pointed into that temp dir. Never touch the real `~/.claude`, `~/.codex`, `~/.config`,
  `~/.local/share/pitwall`, or running Docker containers.
- Never weaken a test or gate. A rewritten test must check the same invariant at least as strictly.
  Never delete a test without listing it in the report with the replacement that covers it.
- Do NOT edit `.secrets.baseline`, `release_acceptance/reviewed-bindings*.json`, or
  `release_acceptance/*-review.json` unless your lane owns them (lane `gates`). If your edits make
  `bind_surfaces`/`check_secrets`/review-record tests fail, say so in the report; the integrator regenerates.
- Do NOT edit CHANGELOG.md or docs/releases/ (integrator).
- Validation: whole-tree `uv run --frozen ruff format --check . && uv run --frozen ruff check .`,
  `uv run --frozen mypy` per pyproject config if your lane touches src, and the tests you touched plus
  `uv run --frozen pytest -q -m "not integration and not slow and not live" -n logical <your test dirs>`.
  Only lane `tests` may run Postgres suites (`make up` stack on 127.0.0.1:5444), serially.

Lane `wf` — workflows, toolchain pins, images.
Owns: `.github/workflows/*`, `.github/dependabot.yml`, `.python-version`, `docker/**`, `docker-compose*.yml`,
`scripts/release/smoke_compose.sh`, `docs/legal/container-image-licenses.md`, `tests/test_dockerfiles.py`,
`.pre-commit-config.yaml`, `tools/ci/check_workflows.py`, `tests/test_workflow_policy.py`.
Fixes: B1/A1 SBOM env from the lock (`UV_PROJECT_ENVIRONMENT=$PWD/artifacts/sbom-env uv sync --frozen
--no-dev --no-install-project` then `uv pip install --no-deps --python artifacts/sbom-env/bin/python dist/*.whl`);
A2 every setup-python uses `python-version-file: .python-version`, drop the one-element matrix and
`--python 3.14.7` args in workflows; A4 drop setup-uv `version:` pins or replace with one source of truth
that Dependabot or a test keeps aligned with Dockerfile `ARG UV_VERSION` (uv >= 0.12.2), and add a test
that the Dockerfiles and workflows agree; A5 `tests/test_dockerfiles.py:31` regex generic, smoke_compose.sh
python assert derived from the Dockerfile tag, license doc test reads the digest from the Dockerfiles (or
the doc line is generated by a command named in the failure message); A6 workflow policy accepts only
setup-python; A7 Dependabot `ignore` for python/postgres/redis major+minor in docker and docker-compose,
bump ruff pre-commit rev to the locked ruff; A8 run the Trivy image scan on PRs that touch docker/ or
uv.lock; A9 explicit `ubuntu-24.04`/`macos-26` runner labels and `python3` (or setup-python) in the
`required` job; A10/A11 move action pins to the commits Dependabot PR #4 proposes (verify each SHA with
`gh api repos/<owner>/<repo>/git/ref/tags/<tag>` peeled to a commit); B3/B4 model-facts: open its PR,
then trigger CI on it with `gh workflow run ci.yml --ref <branch>` (add `workflow_dispatch` to ci.yml
if missing and `actions: write` to the job) and run `tools/security/check_secrets.py` before pushing;
B5 `cancel-in-progress` only for pull_request events; B10 Node 20 deprecation handled by the action bumps.
Node pin (C6): keep `node-version` values but they must equal the single `MIN_NODE` constant lane
`agents` introduces in `src/pitwall/workbench/doctor.py`; add the check in `tests/release/test_workflows.py`
is lane `policy`'s, so only change the YAML here.

Lane `policy` — release-policy tests and test selection.
Owns: `tests/release/conftest.py`, `tests/release/test_release_policy.py`, `tests/release/test_workflows.py`,
`tests/release/test_dependabot.py`, `tests/tools/test_ci_required_gate.py`, `tests/test_release_scripts.py`,
`tests/legal/test_notice_and_sbom.py`, `Makefile`, `docs/sbom/**`, `tests/docs/test_readme_newcomer.py`,
`tests/docs/test_release_runbooks.py`, `tests/release/test_install_references.py`.
Fixes: C1 `get_closest_marker("release")` and a fake item with no marker; C2 `and not live` in Makefile
`test-fast` and `test-cov` plus a test that the Makefile selector equals ci.yml's `test` job selector;
C3 the release-policy files must run on PRs: coordinate by writing the exact ci.yml line change in the
report for lane `wf` is NOT allowed (cross-lane) — instead lane `wf` adds
`tests/release/test_workflows.py tests/release/test_release_policy.py tests/release/test_dco_policy.py`
to the ci.yml `-m release` step; lane `policy` makes those files pass on any tree state; C4 parse YAML
(reuse test_workflows helpers) instead of slicing text; C5 drop the duplicate invariants listed in audit C5
keeping the parsed copies; C6 derive values from the source of truth (node version from the `MIN_NODE`
constant in `src/pitwall/workbench/doctor.py`, job list from the `required` job's `needs`); C7 version
literals from pyproject; B2 SBOM test reads `git show HEAD:uv.lock`-equivalent committed lock or skips
only when `PITWALL_DEPENDENCY_COMPAT=1` (lane `wf` sets it in the dependency-compatibility job); C13
`make sbom` target and the regeneration command in the assertion message; C17 `make regen` (sync_model_facts,
sync_routes, pi-extensions-build, sbom, openapi baseline) and `make regen-bindings`
(`bind_surfaces --accept-reviewed`); C18 nested-pytest timeouts use `HANG_GUARD_SECS`, no output-wording
asserts; `make pi-extensions-build`. Install-string tests: user-facing installs become `--python 3.14`
(lane `docs` changes the docs); update the three tests above accordingly, keeping the URL/version checks.

Lane `gates` — regeneration chains and their messages.
Owns: `tools/security/check_secrets.py`, `.secrets.baseline`, `tools/release_acceptance/**`,
`release_acceptance/**`, `tests/release_acceptance/**`, `tools/agents/sync_routes.py`,
`tools/agents/sync_model_facts.py`, `tools/agents/check_generated.py`, `tests/test_surface_bindings.py`
(wherever the bindings tests live), `docs/sdlc/17-testing-strategy.md`, `docs/agents/CONTRIBUTING.md`.
Fixes: C14 exclude `sha256": "<64 hex>"` lines (and model-facts revision SHA fields, B4) from the secret
scan, regenerate the baseline once, every failure message prints the exact command; C15 review records
re-line automatically and hash the reviewed definition span (AST) instead of the whole file, with an
`--accept-reviewed`-style refresh command, messages print the command; C16 sync_routes/sync_model_facts
stale messages print the regeneration command; C8 count literals in `tests/release_acceptance` derive from
one source.

Lane `tests` — flaky/fragile test bodies.
Owns: `tests/agents/**` (except files lane `agents` creates), `tests/mcp/**`, `tests/db/**`,
`tests/conftest.py`, `tests/test_fast_suite_hermetic.py`, `tests/cost/**`, `tests/models/**`,
`tests/tools/test_smoke_launch_shape.py`, `tests/serve/**`, `tests/test_dep_pin_checker.py`,
`tests/test_deprecated_hf_cli_guard.py`, `tests/workbench/test_admission.py`, `tests/release/test_cli_all_commands_journey.py`,
`tests/test_serve_model_quickstart.py`, `tests/test_cost_reconciliation_docs.py`, `tests/test_selfhosted_docs.py`.
Fixes: D-zombie: `tests/agents/test_mcp_event_channel.py` waits until the launcher is gone (or exit.json
exists) before asserting, no double sample; C8 MCP tool count `81` one tripwire literal imported elsewhere,
same for 485/36/21/16; C9 prose pins → structure (SKILL.md copies identical to the claude host copy;
quickstart commands parsed by the CLI parsers); C10 migration SQL substring tests → schema assertions in
the integration lane via information_schema/pg_constraint, and `pytestmark = pytest.mark.integration` per
DB module replacing `_REAL_DB_TEST_MODULES`; C12 timing windows (Events/handshakes, `HANG_GUARD_SECS`,
assert barriers did not break); C18 (`test_fast_suite_hermetic.py`); C19 `ROOT = Path(__file__)...` instead of
cwd; C20 `/proc` polling → `os.kill(pid, 0)` + `ps -o stat=`. Run the fast suite under `-p randomly`
seeds 1 and 2 and the integration suite once (serially) at the end.

Lane `agents` — agents runtime, plugins, installers.
Owns: `src/pitwall/agents/**`, `plugins/**`, `src/pitwall/agents/**/harness-installers.json` (wherever it
lives), `src/pitwall/workbench/doctor.py`, new tests under `tests/agents/test_brittleness_*.py` and
`tests/plugins/test_brittleness_*.py`.
Fixes: D1 re-pin the five drifted installer hashes from the live vendor URLs (record each fetched sha256
in the report) and allow `releases.openai.com` for codex; D2 `setup harnesses --dry-run` downloads and
hash-checks without running; D3 plugin hook scripts parse on Python 3.9+ (parenthesized excepts) with a
`py39` ruff target for `plugins/` and a test that compiles every hook with `ast.parse(..., feature_version=(3, 9))`;
D4 steer-gate: "not a dispatched harness" early return before the `which("pitwall")` check, fallback to
`~/.local/bin/pitwall`; D5 `agents install` writes the absolute pitwall path (or refuses like `setup mcp`);
D8 empty `XDG_*` treated as unset in `agents/paths.py`, `agents/profiles.py`, `harnesses/goose.py`,
`harnesses/opencode.py`; D12 ps timeout generous and a timeout is not recorded as identity None;
D13 missing harness binary prints the `pitwall agents setup harnesses`/`doctor` hint; D14 git-less
`migrate.py` handles FileNotFoundError; D15 atomic writes in `installation.py`; D19 wheel URL from one
constant derived from `pitwall.__version__`; D20 restricted-mode doctor actually runs bwrap; E12 clone-only
doctor skips say "not applicable to an installed wheel" with no clone remediation; A12/C6 `MIN_NODE`
constant in `workbench/doctor.py` with an accurate message; user-facing install strings in src use
`--python 3.14`; E15 `pitwall agents --help` describes every subcommand and `agents install` lists skipped hosts.

Lane `broker` — broker CLI, personal, gateway, workbench runtime.
Owns: `src/pitwall/cli/**`, `src/pitwall/personal/**`, `src/pitwall/workbench/**` (except doctor.py),
`src/pitwall/cost/**`, `src/pitwall/api/**`, `src/pitwall/doctor.py`, `src/pitwall/mcp_install.py` (wherever
it lives), `src/pitwall/workbench/pi_extensions/**`, new tests under `tests/test_brittleness_*.py` and
`tests/workbench/test_brittleness_*.py`.
Fixes: E6/D10/D11 no tracebacks or opaque codes when the database is missing/unreachable, the API cannot
reach Postgres, the budget env is missing, or HOME is read-only — one clear line naming the fix; D6 gateway
process identity via `agents/pids.process_identity` (macOS-safe); D7 workbench launch fails early on
non-Linux and the flock doctor row is a skip there; D9 top-level doctor reports an invalid pitwall.toml;
D16 `pitwall setup` writes the right profile per shell (zsh/bash login on macOS, fish syntax) with quoting;
D17 one base-URL default honoring `PITWALL_API_PORT`; D18 workbench honors `XDG_STATE_HOME` (Python and
the TS extension, then `make pi-extensions-check`); D21 narrow-terminal tables keep ids readable;
`cost/notifications.py` install string `--python 3.14` from the version constant lane `agents` adds
(import it, do not duplicate).

Lane `docs` — newcomer docs and repo metadata text.
Owns: `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, `RELEASING.md`, `docs/**` except paths owned above
and `docs/superpowers/**`, `docs/evidence/**`, `examples/**`, `plugins/*/README.md`, `docker-compose.testinfra.yml`
header comment is lane `wf`'s (send the replacement text in the report).
Fixes: E1 first dispatch creates its own prompt file; E2/A3 user installs `--python 3.14` plus a
"needs a current uv (`uv self update`)" line; E3 shim PATH; E4 workflow example path; E5 serve doc default
backend first; E7 `uv tool update-shell`; E8 `--wait` in README/CONTRIBUTING (Makefile `up` is lane
`policy`'s); E9 clone URL; E10 fourteen shims; E12 git prerequisite; E13 testinfra teardown step; E14 all
stale text; D7 support matrix wording for the workbench on macOS.

Integration (integrator, after lanes): merge lanes into `fix/release-python-pin`; apply cross-lane
leftovers from reports; `make regen`; `make regen-bindings`; regenerate `.secrets.baseline` through the
lane `gates` command; CHANGELOG 0.3.0a1 additions; full local CI parity (lint job, ruff, mypy, semgrep with
`EIO_BACKEND=posix`, pip-audit, license check, five docker builds, fast suite, integration suite, release
suite, journeys runner); repo settings (Actions may create PRs; description, topics, `needs-qa` label);
push, CI green on PR #11, squash-merge, wait for main's push CI to finish, move `v0.3.0a1` to that commit
(admin bypass), verify with `git tag -v`, push, watch release.yml.
