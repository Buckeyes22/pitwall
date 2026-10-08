from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "mutmut_score_gate", Path(__file__).resolve().parents[1] / "scripts" / "mutmut_score_gate.py"
)
assert _SPEC is not None and _SPEC.loader is not None
gate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gate)


def _stats(tmp_path: Path, **counts: int) -> Path:
    path = tmp_path / "stats.json"
    path.write_text(json.dumps(counts), encoding="utf-8")
    return path


def test_below_floor_fails(tmp_path: Path) -> None:
    stats = _stats(tmp_path, killed=84, survived=16)
    assert gate.main(["--floor", "85", "--stats", str(stats)]) == 1


def test_at_floor_passes(tmp_path: Path) -> None:
    stats = _stats(tmp_path, killed=85, survived=15)
    assert gate.main(["--floor", "85", "--stats", str(stats)]) == 0


def test_timeouts_count_as_not_killed_and_no_tests_are_excluded() -> None:
    score, killed, covered, no_tests = gate.compute_score(
        {"killed": 8, "survived": 1, "timeout": 1, "no_tests": 50}
    )
    assert (score, killed, covered, no_tests) == (80.0, 8, 10, 50)


def test_no_covered_mutants_scores_zero() -> None:
    assert gate.compute_score({"no_tests": 3})[0] == 0.0
