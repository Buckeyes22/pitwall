"""Baseline runner, translated from packages/pi-workbench/tests/baseline-runner.test.ts.

A fake ``pi`` script (selected through ``PITWALL_WORKBENCH_PI_BIN``) plays the RPC child, so the runner
is exercised end to end without a model.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Literal

import pytest

from pitwall.workbench.hosted.baseline import BaselineError, run_baseline
from pitwall.workbench.hosted.fixture import FixtureCheck, create_fixture, fixture_check
from pitwall.workbench.hosted.turn import GateFailure, gate_fixture_check
from tests.hang_guard import HANG_GUARD_SECS

COLORS_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "colors.png"
Mode = Literal["wrong-model", "turn-error", "unrelated-write"]

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")

FAKE_PI = """
import { writeFileSync } from 'node:fs';
let buf = '';
process.stdin.on('data', chunk => { buf += chunk; let i; while ((i = buf.indexOf('\\n')) >= 0) { const line = buf.slice(0, i); buf = buf.slice(i + 1); if (!line) continue; const c = JSON.parse(line); const emit = (x) => process.stdout.write(JSON.stringify(x) + '\\n');
if (c.type === 'get_state') emit({ type: 'response', id: c.id, command: c.type, success: true, data: { model: { provider: 'fixture', id: __MODEL__ }, isStreaming: false, pendingMessageCount: 0 } });
else if (c.type === 'set_auto_retry' || c.type === 'set_auto_compaction') emit({ type: 'response', id: c.id, command: c.type, success: true });
else if (c.type === 'prompt') { emit({ type: 'response', id: c.id, command: c.type, success: true }); emit({ type: 'agent_start' }); if (__WRITE__) { writeFileSync(process.env.BASELINE_FIXTURE + '/add.mjs', 'export const add = (a, b) => a + b;\\n'); writeFileSync(process.env.BASELINE_FIXTURE + '/unrelated.txt', 'unexpected\\n'); emit({ type: 'tool_execution_start', toolName: 'bash' }); emit({ type: 'tool_execution_start', toolName: 'edit' }); } emit({ type: 'message_start', message: { role: 'assistant' } }); emit({ type: 'message_end', message: { role: 'assistant', stopReason: __STOP__, content: [{ type: 'text', text: 'fake' }] } }); emit({ type: 'agent_settled' }); }
else if (c.type === 'compact') { emit({ type: 'response', id: c.id, command: c.type, success: true, data: { summary: 'fake summary' } }); }
else if (c.type === 'follow_up' || c.type === 'set_thinking_level' || c.type === 'get_session_stats') { emit({ type: 'response', id: c.id, command: c.type, success: true, data: {} }); if (c.type === 'follow_up') emit({ type: 'agent_settled' }); }
else if (c.type === 'clear_queue' || c.type === 'abort') emit({ type: 'response', id: c.id, command: c.type, success: true });
}});
process.stdin.on('end', () => process.exit(0));
"""


def make_fixture(root: Path) -> Path:
    cwd = root / "baseline-fixture"
    cwd.mkdir()
    (cwd / "preserve.txt").write_text("USER_UNRELATED_CONTENT_MUST_SURVIVE\n")
    (cwd / "add.mjs").write_text("export const add = (a, b) => a - b;\n")
    (cwd / "add.test.mjs").write_text(
        "import test from 'node:test'; import assert from 'node:assert'; "
        "import { add } from './add.mjs'; test('add', () => assert.equal(add(2, 3), 5));\n"
    )
    return cwd


def fake_pi(root: Path, mode: Mode) -> Path:
    script = root / "fake-pi.mjs"
    source = (
        FAKE_PI.replace(
            "__MODEL__", "'wrong-model'" if mode == "wrong-model" else "'fixture-model'"
        )
        .replace("__WRITE__", "true" if mode == "unrelated-write" else "false")
        .replace("__STOP__", "'error'" if mode == "turn-error" else "'stop'")
    )
    script.write_text(source)
    return script


def run_mode(root: Path, monkeypatch: pytest.MonkeyPatch, mode: Mode) -> tuple[int, dict[str, Any]]:
    cwd = make_fixture(root)
    fake = fake_pi(root, mode)
    report = root / "report.json"
    config = root / "profile.json"
    config.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "profiles": {
                    "local": {
                        "provider": "fixture",
                        "modelId": "fixture-model",
                        "endpoint": "http://127.0.0.1:1/v1",
                        "api": "openai-completions",
                        "apiKeyEnv": "FIXTURE_KEY",  # pragma: allowlist secret
                        "servedContextTokens": 32768,
                        "maxCompletionTokens": 4096,
                        "reasoningLevel": "high",
                        "resourceGroup": "fixture",
                        "allowProviderFallback": False,
                    }
                },
            }
        )
    )
    monkeypatch.setenv("PITWALL_WORKBENCH_PI_BIN", str(fake))
    monkeypatch.setenv("FIXTURE_KEY", "fixture-only")  # pragma: allowlist secret
    monkeypatch.setenv("BASELINE_FIXTURE", str(cwd))
    status = run_baseline(config, "local", cwd, report)
    return status, json.loads(report.read_text())


@pytest.mark.parity
def test_baseline_rejects_a_runtime_selecting_the_wrong_exact_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Source: baseline-runner.test.ts 'baseline rejects a runtime selecting the wrong exact model'."""
    status, report = run_mode(tmp_path, monkeypatch, "wrong-model")
    assert status != 0
    assert report["gates"]["identity"]["status"] == "failed"
    assert report["profile"]["modelId"] == "fixture-model"


@pytest.mark.parity
def test_baseline_marks_an_accepted_but_failed_model_turn_as_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Source: baseline-runner.test.ts 'baseline marks an accepted but failed model turn as failed'."""
    status, report = run_mode(tmp_path, monkeypatch, "turn-error")
    assert status != 0
    assert report["gates"]["repair"]["status"] == "failed"
    assert report["childExited"] is True


@pytest.mark.parity
def test_baseline_rejects_a_repair_that_writes_outside_the_allowed_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Source: baseline-runner.test.ts 'baseline rejects a repair that writes outside the allowed target'."""
    status, report = run_mode(tmp_path, monkeypatch, "unrelated-write")
    assert status != 0
    assert report["gates"]["repair"]["status"] == "failed"
    assert "fixture changed outside add.mjs" in report["gates"]["repair"]["actual"]


def test_create_fixture_writes_the_failing_task_and_refuses_an_existing_path(
    tmp_path: Path,
) -> None:
    """``create-fixture.ts``: a new private directory holding the failing add task and the image."""
    target = tmp_path / "disposable"
    assert create_fixture(target) == target
    assert (target.stat().st_mode & 0o777) == 0o700
    assert (target / "add.mjs").read_text() == "export const add = (a, b) => a - b;\n"
    assert "assert.equal(add(-2, 3), 1)" in (target / "add.test.mjs").read_text()
    assert (target / "preserve.txt").read_text() == "USER_UNRELATED_CONTENT_MUST_SURVIVE\n"
    assert (target / "colors.png").read_bytes() == COLORS_FIXTURE.read_bytes()
    with pytest.raises(FileExistsError):
        create_fixture(target)


def test_created_fixture_fails_its_own_test_until_add_is_repaired(tmp_path: Path) -> None:
    target = create_fixture(tmp_path / "disposable")
    # A hang guard, not the gate's latency: only a wedged check reaches it.
    before = fixture_check(target, HANG_GUARD_SECS)
    assert not before.timed_out and before.exit_code not in (None, 0)
    (target / "add.mjs").write_text("export const add = (a, b) => a + b;\n")
    assert fixture_check(target, HANG_GUARD_SECS) == FixtureCheck(0, False)


def test_fixture_check_reports_a_hang_as_timed_out(tmp_path: Path) -> None:
    target = create_fixture(tmp_path / "disposable")
    # Node cannot start and finish inside a millisecond, so this takes the timeout branch.
    assert fixture_check(target, 0.001) == FixtureCheck(None, True)


def test_a_hung_fixture_check_fails_the_gate_naming_the_check_and_the_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "pitwall.workbench.hosted.turn.fixture_check", lambda cwd: FixtureCheck(None, True)
    )
    with pytest.raises(GateFailure) as raised:
        gate_fixture_check(tmp_path, "fixture test after repair")
    assert str(raised.value) == "fixture test after repair did not finish within 120 s"


def test_a_finished_fixture_check_is_returned_to_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "pitwall.workbench.hosted.turn.fixture_check", lambda cwd: FixtureCheck(1, False)
    )
    assert gate_fixture_check(tmp_path, "fixture test before repair") == FixtureCheck(1, False)


def test_baseline_refuses_a_directory_that_is_not_the_designated_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd = make_fixture(tmp_path)
    (cwd / "preserve.txt").write_text("someone else's file\n")
    config = tmp_path / "profile.json"
    profile = {
        "provider": "fixture",
        "modelId": "fixture-model",
        "endpoint": "http://127.0.0.1:1/v1",
        "api": "openai-completions",
        "apiKeyEnv": "FIXTURE_KEY",  # pragma: allowlist secret
        "servedContextTokens": 32768,
        "maxCompletionTokens": 4096,
        "resourceGroup": "fixture",
        "allowProviderFallback": False,
    }
    config.write_text(json.dumps({"schemaVersion": 1, "profiles": {"local": profile}}))
    monkeypatch.setenv("FIXTURE_KEY", "fixture-only")  # pragma: allowlist secret
    with pytest.raises(BaselineError, match="designated disposable baseline fixture"):
        run_baseline(config, "local", cwd, tmp_path / "report.json")
    monkeypatch.delenv("FIXTURE_KEY")
    with pytest.raises(BaselineError, match="referenced environment variable"):
        run_baseline(config, "local", cwd, tmp_path / "report.json")
