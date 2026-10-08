"""Focused tests for the all-domain release-acceptance composition facade."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from tools.release_acceptance import discovery


def _domain_rows(*rows: dict[str, Any], issues: list[dict[str, Any]] | None = None) -> Any:
    return {
        "schema_version": "fixture.v1",
        "surfaces": list(rows),
        "issues": issues or [],
    }


def _row(surface_id: str, source: str, *, kind: str = "config") -> dict[str, Any]:
    return {
        "surface_id": surface_id,
        "kind": kind,
        "operation": "probe",
        "source": source,
        "metadata": {"contract_sha256": "fixture-contract"},
    }


def _discoverers(*reports: tuple[str, Any]) -> dict[str, Any]:
    empty_report = _domain_rows()
    result = {
        domain: (lambda _root, report=empty_report: report) for domain in discovery.DOMAIN_ORDER
    }
    result.update(
        {
            domain: (
                callable_value
                if callable(callable_value)
                else lambda _root, report=callable_value: report
            )
            for domain, callable_value in reports
        }
    )
    return result


def test_composes_all_domains_with_evidence_fields_and_origin() -> None:
    discoverers = _discoverers(
        ("tui", _domain_rows(_row("tui:probe", "src/pitwall/tui/app.py:1", kind="tui"))),
        ("config", _domain_rows(_row("config:probe", ".env.example:1", kind="config"))),
        (
            "rest_mcp",
            _domain_rows(_row("rest:GET:/probe", "src/pitwall/api/app.py:1", kind="rest")),
        ),
    )

    report = discovery.build_report(Path("/tmp/release-discovery-test"), discoverers=discoverers)
    ids = [row["surface_id"] for row in report["surfaces"]]

    assert ids == ["config:probe", "rest:GET:/probe", "tui:probe"]
    for row in report["surfaces"]:
        assert row["surface_kind"] == row["kind"]
        assert row["declared_source"] == [row["source"]]
        assert row["origin"]["surface_id"] == row["surface_id"]
    assert report["status"] == "complete"
    assert report["issues"] == []
    assert [item["name"] for item in report["domains"]] == list(discovery.DOMAIN_ORDER)


def test_duplicate_semantic_id_is_retained_and_reported() -> None:
    duplicate = _domain_rows(
        _row("cli:probe", "src/pitwall/cli.py:10", kind="cli"),
        _row("cli:probe", "src/pitwall/cli.py:20", kind="cli"),
    )
    report = discovery.build_report(
        Path("/tmp/release-discovery-test"), discoverers=_discoverers(("cli_dispatch", duplicate))
    )

    rows = [row for row in report["surfaces"] if row["surface_id"] == "cli:probe"]
    assert len(rows) == 2
    duplicate_issues = [
        issue for issue in report["issues"] if issue["code"] == "duplicate-surface-id"
    ]
    assert len(duplicate_issues) == 1
    assert duplicate_issues[0]["surface_ids"] == ["cli:probe"]
    assert report["status"] == "unresolved"


def test_cross_domain_collision_is_not_silently_namespaced() -> None:
    same_id = "routing:provider:probe"
    report = discovery.build_report(
        Path("/tmp/release-discovery-test"),
        discoverers=_discoverers(
            ("routing", _domain_rows(_row(same_id, "registry.json:1", kind="provider"))),
            ("routing_contracts", _domain_rows(_row(same_id, "plugin.json:1", kind="plugin"))),
        ),
    )

    rows = [row for row in report["surfaces"] if row["surface_id"] == same_id]
    assert len(rows) == 2
    issue = next(issue for issue in report["issues"] if issue["code"] == "surface-id-collision")
    assert issue["domains"] == ["routing", "routing_contracts"]
    assert {row["origin"]["domain"] for row in rows} == {"routing", "routing_contracts"}


def test_surface_kind_must_match_evidence_vocabulary() -> None:
    report = discovery.build_report(
        Path("/tmp/release-discovery-test"),
        discoverers=_discoverers(
            ("config", _domain_rows(_row("config:bad", "config.py:1", kind="unknown")))
        ),
    )

    issue = next(issue for issue in report["issues"] if issue["code"] == "invalid-surface-kind")
    assert issue["surface_ids"] == ["config:bad"]


def test_declared_source_references_must_match_evidence_shape() -> None:
    row = _row("config:bad-source", "config.py:1")
    row["declared_source"] = ["not a path reference"]
    report = discovery.build_report(
        Path("/tmp/release-discovery-test"),
        discoverers=_discoverers(("config", _domain_rows(row))),
    )

    issue = next(issue for issue in report["issues"] if issue["code"] == "invalid-declared-source")
    assert issue["surface_ids"] == ["config:bad-source"]


def test_failing_domain_is_explicit_and_other_domains_continue() -> None:
    def fail(_root: Path) -> dict[str, Any]:
        raise RuntimeError("fixture extractor failed")

    report = discovery.build_report(
        Path("/tmp/release-discovery-test"),
        discoverers=_discoverers(
            ("gateway", fail),
            ("ops", _domain_rows(_row("ops:probe", "docker-compose.yml:1", kind="ops"))),
        ),
    )

    assert any(row["surface_id"] == "ops:probe" for row in report["surfaces"])
    issue = next(issue for issue in report["issues"] if issue["code"] == "domain-failed")
    assert issue["domain"] == "gateway"
    assert "fixture extractor failed" in issue["message"]
    assert report["domains"][4]["status"] == "unresolved"


def test_order_is_stable_and_domain_issues_are_preserved() -> None:
    issue = {"topic": "scope", "status": "partial", "reason": "fixture remains open"}
    discoverers = _discoverers(
        (
            "config",
            _domain_rows(
                _row("config:z", ".env.example:2"),
                _row("config:a", ".env.example:1"),
                issues=[issue],
            ),
        ),
        ("tui", _domain_rows(_row("tui:a", "src/pitwall/tui/app.py:2"))),
    )
    first = discovery.build_report(Path("/tmp/release-discovery-test"), discoverers=discoverers)
    second = discovery.build_report(Path("/tmp/release-discovery-test"), discoverers=discoverers)

    assert first == second
    assert [row["surface_id"] for row in first["surfaces"]][:2] == ["config:a", "config:z"]
    assert first["issues"][0]["domain"] == "config"
    assert first["deferred_scope"] == first["issues"]


def test_stale_source_contract_is_unresolved(tmp_path: Path) -> None:
    source = tmp_path / "docker-compose.yml"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("SETTING = 1\n", encoding="utf-8")
    original_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    report = _domain_rows(
        {
            **_row("ops:compose:docker-compose.yml:service", "docker-compose.yml:1", kind="ops"),
            "metadata": {"source_sha256": original_digest},
        }
    )
    source.write_text("SETTING = 2\n", encoding="utf-8")

    result = discovery.build_report(
        tmp_path,
        discoverers=_discoverers(("ops", report)),
    )

    issue = next(issue for issue in result["issues"] if issue["code"] == "stale-source-contract")
    assert issue["surface_ids"] == ["ops:compose:docker-compose.yml:service"]
    assert result["status"] == "unresolved"


def test_full_file_source_contract_missing_or_malformed_is_unresolved(tmp_path: Path) -> None:
    missing = _row("ops:missing", "missing.yml:1", kind="ops")
    missing["metadata"] = {"source_sha256": "not-a-digest"}
    malformed = _row("ops:outside", "/etc/passwd:1", kind="ops")
    malformed["metadata"] = {"source_sha256": "0" * 64}
    result = discovery.build_report(
        tmp_path,
        discoverers=_discoverers(("ops", _domain_rows(missing, malformed))),
    )

    codes = {issue["code"] for issue in result["issues"]}
    assert "invalid-source-contract" in codes
    assert "unavailable-source-contract" in codes


def test_explicit_invalid_canonical_fields_are_not_reported_complete(tmp_path: Path) -> None:
    row = _row("config:probe", "settings.py:1")
    row.update(surface_kind="", declared_source="settings.py:1")
    report = discovery.build_report(
        tmp_path, discoverers=_discoverers(("config", _domain_rows(row)))
    )
    assert report["status"] == "unresolved"


def test_source_review_symlink_is_not_read_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "release_acceptance").mkdir(parents=True)
    outside = tmp_path / "private-review.json"
    outside.write_text('{"schema_version":"outside-private-canary","entries":[]}')
    (root / "release_acceptance/discovery-review.json").symlink_to(outside)
    report = discovery.build_report(root, discoverers=_discoverers())
    assert report["status"] == "unresolved"
    assert "outside-private-canary" not in str(report)


def test_unreadable_source_contract_preserves_other_domains(
    tmp_path: Path, monkeypatch: Any
) -> None:
    source = tmp_path / "Dockerfile"
    source.write_text("FROM scratch\n")
    row = _row("install:probe", "Dockerfile:1", kind="install")
    row["metadata"]["source_sha256"] = "a" * 64

    def denied(_path: Path) -> str:
        raise PermissionError("fixture source denied")

    monkeypatch.setattr(discovery, "_file_sha256", denied)
    report = discovery.build_report(
        tmp_path,
        discoverers=_discoverers(
            ("ops", _domain_rows(row)),
            ("tui", _domain_rows(_row("tui:probe", "app.py:1", kind="tui"))),
        ),
    )
    assert report["status"] == "unresolved"
    assert any(row["surface_id"] == "tui:probe" for row in report["surfaces"])
