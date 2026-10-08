"""Comparison runner end to end against a fake ``pi`` (no model, no network).

The fake answers every prompt with text and never touches a file, so every task must be reported
as a failed acceptance while identity, protected-file, and capture checks still pass. These cases
pin the report structure and the option handling of ``pitwall workbench compare``.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from pitwall.workbench.comparison.runner import (
    ComparisonOptions,
    ComparisonUsageError,
    parse_comparison_options,
    run_comparison,
)

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")

FAKE_PI = """
import { writeFileSync } from 'node:fs';
let buf = '';
const touch = () => { const dir = process.env.COMPARISON_FIXTURE; writeFileSync(dir + '/notes.txt', 'FIRST_LINE\\nSECOND_LINE\\n'); writeFileSync(dir + '/add.mjs', 'export const add = (a, b) => a + b;\\n'); writeFileSync(dir + '/add.test.mjs', "import {test} from 'node:test'; import assert from 'node:assert/strict'; import {add} from './add.mjs'; test('add', () => { assert.equal(add(2, 3), 5); assert.equal(add(-2, 3), 1); });\\n"); };
process.stdin.on('data', chunk => { buf += chunk; let i; while ((i = buf.indexOf('\\n')) >= 0) { const line = buf.slice(0, i); buf = buf.slice(i + 1); if (!line) continue; const c = JSON.parse(line); const emit = (x) => process.stdout.write(JSON.stringify(x) + '\\n');
const ok = (data) => emit({ type: 'response', id: c.id, command: c.type, success: true, data });
if (c.type === 'get_state') ok({ model: { provider: 'fixture', id: 'fixture-model' }, sessionId: 'fake-session', isStreaming: false, pendingMessageCount: 0 });
else if (c.type === 'prompt') { touch(); ok(undefined); emit({ type: 'agent_start' }); emit({ type: 'message_start', message: { role: 'assistant' } }); emit({ type: 'message_end', message: { role: 'assistant', stopReason: 'stop', content: [{ type: 'text', text: 'fake answer' }], usage: { input: 10, output: 2 } } }); emit({ type: 'agent_settled' }); }
else if (c.type === 'compact') { emit({ type: 'compaction_end', result: { summary: 'fake summary' } }); ok({ summary: 'fake summary', tokensBefore: 100, estimatedTokensAfter: 10 }); }
else ok({});
}});
process.stdin.on('end', () => process.exit(0));
"""

PROFILE = {
    "provider": "fixture",
    "modelId": "fixture-model",
    "endpoint": "http://127.0.0.1:1/v1",
    "api": "openai-completions",
    "apiKeyEnv": "FIXTURE_KEY",  # pragma: allowlist secret
    "servedContextTokens": 32768,
    "maxCompletionTokens": 4096,
    "reasoningLevel": "low",
    "resourceGroup": "fixture",
    "allowProviderFallback": False,
}


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    fake = tmp_path / "fake-pi.mjs"
    fake.write_text(FAKE_PI)
    path = tmp_path / "profile.json"
    path.write_text(json.dumps({"schemaVersion": 1, "profiles": {"local-coder": PROFILE}}))
    monkeypatch.setenv("PITWALL_WORKBENCH_PI_BIN", str(fake))
    monkeypatch.setenv("FIXTURE_KEY", "fixture-only")  # pragma: allowlist secret
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return path


def report_of(output: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((output / "comparison.json").read_text())
    return loaded


def test_reports_failed_core_tasks_and_intact_checks_for_a_model_that_edits_nothing(
    tmp_path: Path, config: Path
) -> None:
    output = tmp_path / "out"
    summary = run_comparison(
        ComparisonOptions(config, output, repetitions=2, candidates=("A-stock",))
    )
    assert summary["runs"] == 2
    assert summary["candidates"] == ["A-stock"]
    assert summary["unexecuted"] == ["B-nicobailon", "C-tintin"]
    report = report_of(output)
    assert report["schemaVersion"] == 1
    assert report["controls"]["secretValuesPrinted"] is False
    assert (output / "comparison.json").stat().st_mode & 0o777 == 0o600
    assert len(report["results"]) == 2
    for run in report["results"]:
        assert run["candidate"] == "A-stock"
        assert run["identity"] == {"provider": "fixture", "modelId": "fixture-model", "exact": True}
        assert run["coreTasksStatus"] == "failed"
        assert run["overallStatus"] == "partial"
        assert [task["id"] for task in run["tasks"]] == [
            "create-read-edit-read",
            "forced-failure-repair",
            "medium-search",
        ]
        assert {task["acceptance"] for task in run["tasks"]} == {"failed"}
        assert run["protectedUnchanged"] is True
        assert run["protectedTreeUnchanged"] is True
        assert run["filesChangedOutsideExpected"] == []
        assert run["capture"]["complete"] is True
        assert run["child"]["status"] == "unexecuted"
        assert run["child"]["reason"] == "stock Pi has no child surface"
        assert run["compact"]["status"] == "failed"
        assert run["usage"]["assistantMessages"] > 0
        assert run["usage"]["input"] == 10 * run["usage"]["assistantMessages"]
        assert run["accounting"]["instrumented"] is False
        assert run["mediumFixture"]["files"] == 232
        assert run["stderrBytes"] == 0


def test_child_only_batch_skips_core_tasks_and_compaction(tmp_path: Path, config: Path) -> None:
    output = tmp_path / "out"
    run_comparison(
        ComparisonOptions(config, output, repetitions=2, candidates=("C-tintin",), child_only=True)
    )
    report = report_of(output)
    assert report["childOnly"] is True
    for run in report["results"]:
        assert run["coreTasksStatus"] == "unexecuted"
        assert run["acceptedTaskRate"] == "unexecuted"
        assert run["compact"] == {"status": "unexecuted", "reason": "child-only batch"}
        assert run["child"]["status"] == "unexecuted"
        assert run["child"]["launchStatus"] == "unexecuted"
        assert run["child"]["reason"] == "model did not invoke a candidate child surface"
        assert run["overallStatus"] == "partial"


def test_warm_batch_reports_the_second_pass_as_warm_in_one_session(
    tmp_path: Path, config: Path
) -> None:
    output = tmp_path / "out"
    run_comparison(
        ComparisonOptions(config, output, repetitions=2, candidates=("A-stock",), warm=True)
    )
    rows = report_of(output)["results"]
    assert [(row["repetition"], row["warm"], row["freshSession"]) for row in rows] == [
        (1, False, True),
        (2, True, False),
    ]
    assert rows[0]["warmAcrossRepetitions"]["taskFilesRestored"] == [
        "notes.txt",
        "add.mjs",
        "add.test.mjs",
    ]
    # The fake reports a session id, which stays the same across both passes in one process.
    assert rows[0]["warmAcrossRepetitions"]["sameSession"] is True
    assert rows[0]["warmAcrossRepetitions"]["status"] == "passed"
    assert rows[0]["overallStatus"] == "partial"


def test_automatic_compaction_run_without_a_threshold_event_is_reported_failed(
    tmp_path: Path, config: Path
) -> None:
    output = tmp_path / "out"
    run_comparison(
        ComparisonOptions(
            config,
            output,
            repetitions=2,
            candidates=("A-stock",),
            automatic_compact_only=True,
            automatic_prelude_turns=1,
            automatic_filler_words=5,
            automatic_final_filler_words=5,
        )
    )
    report = report_of(output)
    assert report["controls"]["automaticSchedule"]["preludeTurns"] == 1
    assert report["controls"]["derivedCompaction"]["enabled"] is True
    for run in report["results"]:
        assert run["compact"]["status"] == "failed"
        assert "automatic threshold compaction did not fire" in run["compact"]["reason"]
        assert run["profile"]["derivedCompaction"]["reserveTokens"] > 0


def test_requires_the_profile_credential_in_the_runner_environment(
    tmp_path: Path, config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FIXTURE_KEY")
    with pytest.raises(ComparisonUsageError, match="FIXTURE_KEY"):
        run_comparison(ComparisonOptions(config, tmp_path / "out", candidates=("A-stock",)))


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (("config.json", "out", "1"), "repetitions must be 2 or 3"),
        (("config.json", "out", "4"), "repetitions must be 2 or 3"),
        (("config.json", "out", "many"), "repetitions must be 2 or 3"),
        (("config.json", "out", "4", "--bounded-single"), "repetitions must be 1, 2, or 3"),
        (
            ("config.json", "out", "2", "--compact-only", "--automatic-compact-only"),
            "choose either --compact-only or --automatic-compact-only",
        ),
        (
            ("config.json", "out", "2", "--automatic-prelude-turns=0"),
            "--automatic-prelude-turns= requires a positive integer",
        ),
        (
            ("config.json", "out", "3", "--warm"),
            "--warm runs the core tasks twice in one session",
        ),
        (
            ("config.json", "out", "2", "--warm", "--child-only"),
            "--warm runs the core tasks twice in one session",
        ),
    ],
)
def test_rejects_unusable_option_combinations(arguments: tuple[str, ...], message: str) -> None:
    with pytest.raises(ComparisonUsageError, match=message):
        parse_comparison_options(*arguments)


def test_parses_flags_numeric_options_and_candidate_ids() -> None:
    options = parse_comparison_options(
        "config.json",
        "out",
        "1",
        "--bounded-single",
        "--child-only",
        "--workbench-admission",
        "--automatic-filler-words=10",
        "--automatic-settle-timeout-ms=5000",
        "B-nicobailon",
    )
    assert options.repetitions == 1
    assert options.bounded_single and options.child_only and options.workbench_admission
    assert options.automatic_filler_words == 10
    assert options.automatic_settle_timeout_ms == 5000
    assert options.candidates == ("B-nicobailon",)


def test_rejects_unknown_candidates_and_stock_child_only(tmp_path: Path, config: Path) -> None:
    with pytest.raises(ComparisonUsageError, match="unknown candidate; choose"):
        run_comparison(ComparisonOptions(config, tmp_path / "out", candidates=("D-other",)))
    with pytest.raises(ComparisonUsageError, match="stock Pi has no child surface"):
        run_comparison(
            ComparisonOptions(config, tmp_path / "out", candidates=("A-stock",), child_only=True)
        )
