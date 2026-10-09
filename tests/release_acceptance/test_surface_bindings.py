"""Every discovered surface is bound, and the committed bindings are the ones the rules make.

A new REST route, MCP tool, CLI command, configuration key, or any other discovered surface
fails this test until a fixture rule or an entry in ``release_acceptance/surface-test-map.json``
binds it to the test that proves it and ``bind_surfaces`` has been rerun
(``make regen-bindings``, or ``uv run --frozen python -m
tools.release_acceptance.bind_surfaces --accept-reviewed``). File hashes are
not compared for ``reviewed-bindings.json``: the harness's matrix gate refuses a stale binding.
The per-family review records (``reviewed-bindings-*.json``) feed no gate, so their hashes,
definition lines, and test nodes are pinned here instead.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from tools.release_acceptance import bind_surfaces, test_index


def _pairs(bindings: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    return sorted(
        (str(b["surface_id"]), str(b["test_node_id"]), str(b["source"])) for b in bindings
    )


def test_every_surface_is_bound_to_the_committed_tests() -> None:
    payload, unmapped, _stale = bind_surfaces.build()
    assert unmapped == [], "bind these surfaces (fixture rule or surface-test-map.json)"
    committed = json.loads(bind_surfaces.BINDINGS.read_text(encoding="utf-8"))["bindings"]
    assert _pairs(payload["bindings"]) == _pairs(committed), (
        f"committed bindings are stale; {bind_surfaces.REGEN_HINT} and commit "
        "release_acceptance/reviewed-bindings*.json"
    )


def test_family_review_records_match_the_current_tests() -> None:
    payloads, stale = bind_surfaces.build_families()
    assert payloads, "no reviewed-bindings-*.json review record was found"
    assert stale == [], f"re-read each listed test, then {bind_surfaces.REGEN_HINT}: {stale}"
    for path, payload in payloads.items():
        committed = json.loads(path.read_text(encoding="utf-8"))
        assert payload == committed, f"{bind_surfaces.REGEN_HINT} to re-line {path.name}"


def test_every_current_main_binding_names_its_tests_definition_line() -> None:
    lines = bind_surfaces._node_lines(test_index.build_report(bind_surfaces.ROOT))
    committed = json.loads(bind_surfaces.BINDINGS.read_text(encoding="utf-8"))["bindings"]
    drifted = []
    for binding in committed:
        base = str(binding["test_node_id"]).split("[", 1)[0]
        path = base.split("::", 1)[0]
        current = hashlib.sha256((bind_surfaces.ROOT / path).read_bytes()).hexdigest()
        if (
            binding["test_source_sha256"] == current
            and binding["source"] != f"{path}:{lines[base]}"
        ):
            drifted.append(f"{binding['source']} -> {path}:{lines[base]}")
    assert drifted == [], f"{bind_surfaces.REGEN_HINT} to re-line: {drifted}"


def _reviewed(node: str, source: str, sha: str = "0" * 64) -> dict[str, Any]:
    return {
        "surface_id": "rest:GET:/v1/jobs/{workload_id}",
        "test_node_id": node,
        "source": source,
        "test_source_sha256": sha,
        "reviewed_oracle": "unknown workload returns HTTP 404",
    }


def _family_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, row: dict[str, Any]) -> None:
    record = {"schema_version": "reviewed-test-bindings.v1", "bindings": [row]}
    (tmp_path / "reviewed-bindings-probe.json").write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setattr(bind_surfaces, "BINDINGS", tmp_path / "reviewed-bindings.json")


@pytest.mark.parametrize(
    "path",
    ["tests/api/test_jobs_contract_deleted.py", "tests/api_renamed/test_jobs_contract.py"],
    ids=["deleted", "renamed"],
)
def test_a_family_binding_whose_test_file_is_gone_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    node = path + "::test_get_job_unknown_404"
    _family_dir(tmp_path, monkeypatch, _reviewed(node, f"{path}:142"))
    with pytest.raises(SystemExit, match=f"-> {node}: test file {path} is gone"):
        bind_surfaces.build_families()


def test_a_main_binding_whose_test_file_is_gone_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = json.loads(bind_surfaces.BINDINGS.read_text(encoding="utf-8"))
    path = "tests/api/test_jobs_contract_deleted.py"
    node = path + "::test_get_job_unknown_404"
    payload["bindings"].append(_reviewed(node, f"{path}:142"))
    main = tmp_path / "reviewed-bindings.json"
    main.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(bind_surfaces, "BINDINGS", main)
    with pytest.raises(SystemExit, match=f"-> {node}: test file {path} is gone") as raised:
        bind_surfaces.build()
    assert "point each binding's test_node_id" in str(raised.value).lower()
    assert "rerun with --accept-reviewed" in str(raised.value)


def _probe_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> str:
    test_file = tmp_path / "tests" / "test_probe.py"
    test_file.parent.mkdir()
    test_file.write_text(body, encoding="utf-8")
    monkeypatch.setattr(bind_surfaces, "ROOT", tmp_path)
    return hashlib.sha256(test_file.read_bytes()).hexdigest()


def test_a_drifted_source_line_is_re_lined_when_the_hash_is_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sha = _probe_root(tmp_path, monkeypatch, "\n\ndef test_x() -> None:\n    assert True\n")
    row = _reviewed("tests/test_probe.py::test_x", "tests/test_probe.py:1", sha)
    stale = bind_surfaces._refresh_reviewed(
        [row], {"tests/test_probe.py::test_x": 3}, accept_reviewed=False
    )
    assert stale == []
    assert row["source"] == "tests/test_probe.py:3"
    assert row["test_source_sha256"] == sha


def test_re_lining_never_re_accepts_a_changed_test_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _probe_root(tmp_path, monkeypatch, "\n\ndef test_x() -> None:\n    assert False\n")
    reviewed_sha = hashlib.sha256(b"def test_x() -> None:\n    assert True\n").hexdigest()
    row = _reviewed("tests/test_probe.py::test_x", "tests/test_probe.py:1", reviewed_sha)
    stale = bind_surfaces._refresh_reviewed(
        [row], {"tests/test_probe.py::test_x": 3}, accept_reviewed=False
    )
    assert stale == [f"{row['surface_id']} -> tests/test_probe.py::test_x"]
    assert row["source"] == "tests/test_probe.py:1"
    assert row["test_source_sha256"] == reviewed_sha
