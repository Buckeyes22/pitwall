"""Offline reevaluation of preserved comparison reports (``comparison-reevaluate.ts`` and ``comparison-child-reevaluate.ts``)."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from pitwall.workbench.comparison.reevaluate import (
    ReevaluationError,
    reevaluate_child_only,
    reevaluate_comparison,
)

MARKER = "CHILD_ORACLE_7F31"
TARGET = "medium/sector-7/node/target.mjs"
CHILD_ID = "ba205c9f-a8bc-42f0-9d11-000000000001"


def jsonl(records: list[dict[str, Any]]) -> str:
    return "\n".join(json.dumps(record) for record in records) + "\n"


def stamp(second: int) -> str:
    return f"2026-09-20T03:00:{second:02d}.000Z"


def user(text: str, second: int) -> dict[str, Any]:
    return {
        "type": "message",
        "timestamp": stamp(second),
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }


def assistant(
    second: int, text: str | None = None, call: tuple[str, str, dict[str, Any]] | None = None
) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    if call is not None:
        content.append({"type": "toolCall", "id": call[0], "name": call[1], "arguments": call[2]})
    if text is not None:
        content.append({"type": "text", "text": text})
    return {
        "type": "message",
        "timestamp": stamp(second),
        "message": {"role": "assistant", "content": content, "usage": {"input": 5, "output": 1}},
    }


def result(second: int, call_id: str, tool: str, text: str) -> dict[str, Any]:
    return {
        "type": "message",
        "timestamp": stamp(second),
        "message": {
            "role": "toolResult",
            "toolCallId": call_id,
            "toolName": tool,
            "isError": False,
            "content": [{"type": "text", "text": text}],
        },
    }


def build_run(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A preserved batch directory with one A-stock run whose three core tasks were accepted."""
    report = tmp_path / "comparison.json"
    run = tmp_path / "comparison-run-x" / "A-stock-rep-1-y"
    fixture = run / "fixture-abc"
    (fixture / "medium").mkdir(parents=True)
    sessions = run / "agent" / "sessions"
    sessions.mkdir(parents=True)
    failing = "not ok 1 - add\n  Expected values to be strictly equal\n# pass 0\n# fail 1"
    passing = "# pass 1\n# fail 0"
    records = [
        {"type": "session", "cwd": str(fixture)},
        user("create notes", 1),
        assistant(1, call=("c1", "write", {"path": "notes.txt", "content": "FIRST_LINE"})),
        result(1, "c1", "write", "ok"),
        user("append", 2),
        assistant(2, call=("c2", "edit", {"path": "notes.txt"})),
        result(2, "c2", "edit", "ok"),
        user("report", 3),
        assistant(3, call=("c3", "read", {"path": "notes.txt"})),
        result(3, "c3", "read", "FIRST_LINE\nSECOND_LINE"),
        user("write failing tests", 4),
        assistant(4, call=("c4", "bash", {"command": "node --test add.test.mjs"})),
        result(4, "c4", "bash", failing),
        user("repair", 5),
        assistant(5, call=("c5", "bash", {"command": "node --test add.test.mjs"})),
        result(5, "c5", "bash", passing),
        user("search", 6),
        assistant(6, call=("c6", "bash", {"command": f"cat {TARGET}"})),
        result(6, "c6", "bash", "export function add(left, right) {\n  return left + right;\n}"),
        assistant(7, text=f"{TARGET} exports add and return left + right"),
    ]
    (sessions / "parent.jsonl").write_text(jsonl(records))
    row = {
        "candidate": "A-stock",
        "repetition": 1,
        "overallStatus": "partial",
        "tasks": [
            {"effect": {"notes": "FIRST_LINE\nSECOND_LINE\n"}},
            {
                "effect": {
                    "testsHaveOracle": True,
                    "hostOracleExitCode": 0,
                    "testsUnchanged": True,
                    "protectedUnchanged": True,
                }
            },
            {"effect": {"oracleExitCode": 0}},
        ],
        "profile": {"provider": "fixture", "modelId": "fixture-model"},
    }
    report.write_text(json.dumps({"generatedAt": "2026-09-19T00:00:00.000Z", "results": [row]}))
    return report, run, fixture


def test_reevaluates_core_tasks_from_the_persisted_parent_session(tmp_path: Path) -> None:
    report, run, fixture = build_run(tmp_path)
    output = tmp_path / "reevaluated.json"
    result_report = reevaluate_comparison(report, output)
    assert json.loads(output.read_text()) == result_report
    assert (output.stat().st_mode & 0o777) == 0o600
    (row,) = result_report["results"]
    assert row["taskStatus"] == {
        "create-read-edit-read": "passed",
        "forced-failure-repair": "passed",
        "medium-search": "passed",
    }
    assert row["acceptedTaskRate"] == "3/3"
    assert row["classification"] == "reevaluated"
    assert row["compact"] == "unavailable"
    assert row["child"] == "unavailable"
    assert row["fixture"] == str(fixture)
    assert row["parentSession"] == str(run / "agent" / "sessions" / "parent.jsonl")
    assert row["rawSessionUsage"] == {
        "assistantMessages": 7,
        "reportedMessages": 7,
        "input": 35,
        "output": 7,
    }
    assert row["phaseTiming"]["prompts"][0]["prompt"] == "create notes"
    assert row["phaseTiming"]["prompts"][0]["firstUsefulActionMs"] == 0
    aggregate = result_report["aggregate"]
    assert (aggregate["acceptedTasks"], aggregate["taskCount"], aggregate["percentage"]) == (
        3,
        3,
        100.0,
    )
    assert result_report["originalReportUnmodified"] is True


def test_rejects_medium_search_without_a_correlated_read_and_repair_without_a_failure(
    tmp_path: Path,
) -> None:
    report, run, _ = build_run(tmp_path)
    session = run / "agent" / "sessions" / "parent.jsonl"
    text = (
        session.read_text()
        .replace("not ok 1", "SyntaxError")
        .replace("Expected values to be strictly equal", "unexpected token")
    )
    text = text.replace("return left + right;", "return 0;")
    session.write_text(text)
    (row,) = reevaluate_comparison(report, tmp_path / "out.json")["results"]
    assert row["taskStatus"]["forced-failure-repair"] == "failed"
    assert row["taskStatus"]["medium-search"] == "failed"
    assert row["taskStatus"]["create-read-edit-read"] == "passed"


def test_reports_historical_classifications_and_missing_fixtures(tmp_path: Path) -> None:
    report, _, _ = build_run(tmp_path)
    original = json.loads(report.read_text())
    original["generatedAt"] = "2026-09-20T03:11:30.011Z"
    report.write_text(json.dumps(original))
    (row,) = reevaluate_comparison(report, tmp_path / "out.json")["results"]
    assert row["classification"] == "historical-timeout-abort"


def test_a_run_without_a_fixture_is_unavailable(tmp_path: Path) -> None:
    report, _, fixture = build_run(tmp_path)
    shutil.rmtree(fixture)
    (row,) = reevaluate_comparison(report, tmp_path / "out.json")["results"]
    assert row == {
        "candidate": "A-stock",
        "repetition": 1,
        "status": "unavailable",
        "reason": "no fixture path in preserved report",
    }


def test_a_fixture_without_a_matching_parent_session_is_an_error(tmp_path: Path) -> None:
    report, run, _ = build_run(tmp_path)
    (run / "agent" / "sessions" / "parent.jsonl").unlink()
    with pytest.raises(ReevaluationError, match="parent session not found"):
        reevaluate_comparison(report, tmp_path / "out.json")


def child_run(tmp_path: Path, *, sibling: bool = False) -> tuple[Path, Path]:
    run = tmp_path / "batch-dir"
    fixture = run / "fixture-abc"
    (fixture / ".pi").mkdir(parents=True)
    parent = run / "agent" / "parent.jsonl"
    parent.parent.mkdir(parents=True)
    parent.write_text(jsonl([{"type": "session", "cwd": str(fixture)}]))

    def child_session(name: str) -> str:
        return jsonl(
            [
                {"type": "session", "timestamp": stamp(1), "parentSession": str(parent)},
                {"type": "session_info", "name": name},
                {"type": "thinking_level_change", "thinkingLevel": "low"},
                {
                    "type": "message",
                    "id": "m1",
                    "timestamp": stamp(2),
                    "message": {
                        "role": "assistant",
                        "usage": {"input": 4, "output": 2},
                        "content": [
                            {
                                "type": "toolCall",
                                "id": "r1",
                                "name": "read",
                                "arguments": {"path": "child-probe.txt"},
                            }
                        ],
                    },
                },
                {
                    "type": "message",
                    "timestamp": stamp(4),
                    "message": {
                        "role": "toolResult",
                        "toolCallId": "r1",
                        "isError": False,
                        "content": [{"type": "text", "text": MARKER}],
                    },
                },
            ]
        )

    (run / "agent" / "child.jsonl").write_text(child_session(f"Explore#{CHILD_ID[:8]}"))
    if sibling:
        (run / "agent" / "copy.jsonl").write_text(child_session(f"Explore#{CHILD_ID[:8]}"))
    row = {
        "candidate": "C-tintin",
        "repetition": 1,
        "overallStatus": "partial",
        "childReasoningFixture": {"settingsPath": str(fixture / ".pi" / "settings.json")},
        "identity": {"exact": True},
        "protectedUnchanged": True,
        "protectedTreeUnchanged": True,
        "capture": {"complete": True},
        "child": {
            "status": "passed",
            "modelObserved": True,
            "parentReasoningLevel": "low",
            "toolResult": {"childId": CHILD_ID, "completed": True},
        },
        "phases": {"fresh": {"status": "measured"}},
    }
    source = tmp_path / "child-only.json"
    source.write_text(json.dumps({"childOnly": True, "results": [row]}))
    return source, run / "agent" / "child.jsonl"


def test_child_only_reevaluation_selects_the_exact_tintin_child_session(tmp_path: Path) -> None:
    source, child = child_run(tmp_path)
    before = source.read_bytes()
    output = tmp_path / "corrected.json"
    report = reevaluate_child_only(source, output)
    assert source.read_bytes() == before
    assert report["sourceOriginalReportSha256"] == hashlib.sha256(before).hexdigest()
    assert report["aggregate"] == {"passedRuns": 1, "totalRuns": 1, "rate": "1/1"}
    (row,) = report["results"]
    assert row["overallStatus"] == "passed"
    assert row["child"]["childThinkingLevels"] == ["low"]
    assert row["child"]["reasoningControlMatch"] is True
    assert row["child"]["pairedReadProof"] is True
    assert row["child"]["persistedChildEvidence"]["correlated"] is True
    assert row["child"]["persistedChildEvidence"]["files"] == [str(child)]
    assert row["child"]["persistedChildIntervalMs"] == 3000
    assert row["child"]["childUsage"]["input"] == 4
    assert row["phases"]["child"] == row["child"]
    assert (
        row["childReevaluation"]["childSessionSha256"]
        == hashlib.sha256(child.read_bytes()).hexdigest()
    )


def test_child_only_reevaluation_refuses_an_ambiguous_sibling_session(tmp_path: Path) -> None:
    source, _ = child_run(tmp_path, sibling=True)
    (row,) = reevaluate_child_only(source, tmp_path / "corrected.json")["results"]
    assert row["overallStatus"] == "partial"
    assert row["child"]["persistedChildEvidence"]["correlated"] is False
    assert row["child"]["reason"] == "authoritative child session unavailable or ambiguous"
    assert row["childReevaluation"]["childSession"] is None


def test_child_only_reevaluation_never_overwrites_its_source_and_needs_a_child_only_report(
    tmp_path: Path,
) -> None:
    source, _ = child_run(tmp_path)
    with pytest.raises(ReevaluationError, match="must differ"):
        reevaluate_child_only(source, source)
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"childOnly": False, "results": []}))
    with pytest.raises(ReevaluationError, match="not a child-only comparison report"):
        reevaluate_child_only(other, tmp_path / "corrected.json")


def _alias(kind: str, source: Path, tmp_path: Path) -> Path:
    if kind == "same":
        return source
    alias = tmp_path / f"{kind}-alias.json"
    if kind == "symlink":
        alias.symlink_to(source)
    else:
        alias.hardlink_to(source)
    return alias


@pytest.mark.parametrize("kind", ["same", "symlink", "hardlink"])
def test_comparison_reevaluation_never_overwrites_its_source_through_an_alias(
    tmp_path: Path, kind: str
) -> None:
    report, _run, _fixture = build_run(tmp_path)
    before = report.read_bytes()
    with pytest.raises(ReevaluationError, match="must differ"):
        reevaluate_comparison(report, _alias(kind, report, tmp_path))
    assert report.read_bytes() == before


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_child_only_reevaluation_never_overwrites_its_source_through_an_alias(
    tmp_path: Path, kind: str
) -> None:
    source, _ = child_run(tmp_path)
    before = source.read_bytes()
    with pytest.raises(ReevaluationError, match="must differ"):
        reevaluate_child_only(source, _alias(kind, source, tmp_path))
    assert source.read_bytes() == before


def test_a_distinct_output_is_written_privately_without_a_temp_file_left_behind(
    tmp_path: Path,
) -> None:
    report, _run, _fixture = build_run(tmp_path)
    output = tmp_path / "out" / "reevaluated.json"
    output.parent.mkdir()
    reevaluate_comparison(report, output)
    assert (output.stat().st_mode & 0o777) == 0o600
    assert [p.name for p in output.parent.iterdir()] == ["reevaluated.json"]


def test_the_report_is_fsynced_before_it_replaces_the_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    order: list[str] = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(os, "fsync", lambda fd: (order.append("fsync"), real_fsync(fd))[1])
    monkeypatch.setattr(
        os, "replace", lambda a, b: (order.append("replace"), real_replace(a, b))[1]
    )
    report, _run, _fixture = build_run(tmp_path)
    reevaluate_comparison(report, tmp_path / "out.json")
    assert order == ["fsync", "replace"]


def _nicobailon_child(
    tmp_path: Path, *, child: str, parent: str | None, fixture: Path, parent_path: Path
) -> Path:
    """A preserved B-nicobailon child session whose header names ``child`` under ``parent``."""
    header: dict[str, Any] = {
        "type": "session",
        "cwd": str(fixture),
        "parentSession": parent if parent is not None else str(parent_path),
        "childId": child,
    }
    path = tmp_path / "preserved-child.jsonl"
    path.write_text(
        jsonl(
            [
                header,
                {"type": "model_change", "provider": "fixture", "modelId": "fixture-model"},
                {"type": "thinking_level_change", "thinkingLevel": "low"},
                {
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "model": "fixture-model",
                        "content": [
                            {
                                "type": "toolCall",
                                "id": "r1",
                                "name": "read",
                                "arguments": {"path": "child-probe.txt"},
                            }
                        ],
                    },
                },
                {
                    "type": "message",
                    "message": {
                        "role": "toolResult",
                        "toolCallId": "r1",
                        "isError": False,
                        "content": [{"type": "text", "text": MARKER}],
                    },
                },
            ]
        )
    )
    return path


def _nicobailon_row(session: Path, fixture: Path) -> dict[str, Any]:
    return {
        "candidate": "B-nicobailon",
        "repetition": 1,
        "overallStatus": "partial",
        "identity": {"exact": True},
        "protectedUnchanged": True,
        "protectedTreeUnchanged": True,
        "capture": {"complete": True},
        "childReasoningFixture": {"settingsPath": str(fixture / ".pi" / "settings.json")},
        "profile": {"provider": "fixture", "modelId": "fixture-model"},
        "child": {
            "status": "passed",
            "modelObserved": True,
            "parentReasoningLevel": "low",
            "toolResult": {"childId": CHILD_ID, "completed": True},
            "persistedChildEvidence": {"files": [str(session)]},
        },
    }


@pytest.mark.parametrize(
    ("child", "parent", "accepted"),
    [
        (CHILD_ID, None, True),
        ("another-child", None, False),
        (CHILD_ID, "/unrelated/parent.jsonl", False),
    ],
    ids=["valid", "wrong-child", "wrong-parent"],
)
def test_child_only_reevaluation_validates_the_nicobailon_session_header(
    tmp_path: Path, child: str, parent: str | None, accepted: bool
) -> None:
    source, _ = child_run(tmp_path)
    run = tmp_path / "batch-dir"
    fixture = run / "fixture-abc"
    session = _nicobailon_child(
        tmp_path,
        child=child,
        parent=parent,
        fixture=fixture,
        parent_path=run / "agent" / "parent.jsonl",
    )
    source.write_text(
        json.dumps({"childOnly": True, "results": [_nicobailon_row(session, fixture)]})
    )
    (row,) = reevaluate_child_only(source, tmp_path / "corrected.json")["results"]
    assert row["child"]["persistedChildEvidence"]["correlated"] is accepted
    assert (row["overallStatus"] == "passed") is accepted


@pytest.mark.parametrize(
    ("child", "parent", "accepted"),
    [
        (CHILD_ID, None, True),
        ("another-child", None, False),
        (CHILD_ID, "/unrelated/parent.jsonl", False),
    ],
    ids=["valid", "wrong-child", "wrong-parent"],
)
def test_comparison_reevaluation_validates_the_nicobailon_session_header(
    tmp_path: Path, child: str, parent: str | None, accepted: bool
) -> None:
    report, run, fixture = build_run(tmp_path)
    renamed = run.rename(run.parent / "B-nicobailon-rep-1-y")
    fixture = renamed / fixture.name
    parent_path = renamed / "agent" / "sessions" / "parent.jsonl"
    parent_path.write_text(parent_path.read_text().replace(str(run / fixture.name), str(fixture)))
    session = _nicobailon_child(
        tmp_path, child=child, parent=parent, fixture=fixture, parent_path=parent_path
    )
    row = _nicobailon_row(session, fixture)
    row["tasks"] = json.loads(report.read_text())["results"][0]["tasks"]
    report.write_text(json.dumps({"generatedAt": "2026-09-19T00:00:00.000Z", "results": [row]}))
    (out,) = reevaluate_comparison(report, tmp_path / "reevaluated.json")["results"]
    assert out["child"] == ("passed" if accepted else "unavailable")
