"""The Python gateway inventory reads the literal route, shape, and compression declarations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tools.release_acceptance import gateway_inventory as gi

REAL_ROUTES = {
    ("/v1/chat/completions", "POST", "self._chat"),
    ("/v1/models", "GET", "self._models"),
    ("/v1/embeddings", "POST", "self._embeddings"),
    ("/health", "GET", "self._health"),
    ("/internal/telemetry", "GET", "self._telemetry"),
}
REAL_SHAPES = {"openai", "claude", "gemini", "responses"}
REAL_MODES = {"off", "rtk", "caveman", "stacked"}

APP = """class App:
    def __init__(self):
        self._routes: dict[str, tuple] = {
            "/v1/probe": ("POST", self._probe, True),
        }
"""
TRANSLATION = 'INBOUND_SHAPES: tuple[str, ...] = ("openai",)\n'
COMPRESSION = 'CompressionPolicy = Literal["off", "rtk"]\n'


def _by_category(rows: list[dict[str, Any]], category: str) -> list[dict[str, Any]]:
    return [row for row in rows if row["metadata"]["category"] == category]


def _fixture(tmp_path: Path, app: str = APP) -> Path:
    for relative, text in (
        (gi.APP, app),
        (gi.TRANSLATION, TRANSLATION),
        (gi.COMPRESSION, COMPRESSION),
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return tmp_path


def test_real_gateway_routes_shapes_and_modes() -> None:
    rows = gi.discover(gi.ROOT)
    assert {row["kind"] for row in rows} == {"gateway"}
    routes = _by_category(rows, "route")
    assert {
        (row["metadata"]["path"], row["operation"], row["metadata"]["handler"]) for row in routes
    } == REAL_ROUTES
    assert {row["operation"] for row in _by_category(rows, "inbound-shape")} == REAL_SHAPES
    assert {row["operation"] for row in _by_category(rows, "compression-mode")} == REAL_MODES
    assert rows == sorted(rows, key=lambda row: row["surface_id"])
    for row in rows:
        path, _, line = row["source"].rpartition(":")
        assert (gi.ROOT / path).is_file() and int(line) >= 1
        assert len(row["metadata"]["declaration_sha256"]) == 64


def test_surface_ids_keep_the_retired_node_gateway_identities() -> None:
    ids = {row["surface_id"] for row in gi.discover(gi.ROOT)}
    assert "gateway:route:/v1/models:GET" in ids
    assert "gateway:inbound-shape:claude" in ids
    assert "gateway:compression-mode:stacked" in ids


def test_changed_route_method_changes_the_row_and_digest(tmp_path: Path) -> None:
    base = {r["surface_id"]: r for r in gi.discover(_fixture(tmp_path))}
    assert "gateway:route:/v1/probe:POST" in base
    changed = {
        r["surface_id"]: r for r in gi.discover(_fixture(tmp_path, APP.replace("POST", "GET")))
    }
    assert "gateway:route:/v1/probe:GET" in changed
    assert "gateway:route:/v1/probe:POST" not in changed

    flag = {
        r["surface_id"]: r for r in gi.discover(_fixture(tmp_path, APP.replace("True", "False")))
    }
    first = base["gateway:route:/v1/probe:POST"]["metadata"]["declaration_sha256"]
    assert flag["gateway:route:/v1/probe:POST"]["metadata"]["declaration_sha256"] != first


def test_a_comment_or_string_example_is_never_a_route(tmp_path: Path) -> None:
    app = APP.replace(
        "        self._routes",
        '        # "/v9/ghost": ("GET", self._ghost, True),\n        example = \'"/v9/example": 1\'\n'
        "        self._routes",
    )
    ids = {r["surface_id"] for r in gi.discover(_fixture(tmp_path, app))}
    assert ids >= {"gateway:route:/v1/probe:POST"}
    assert not [i for i in ids if "/v9/" in i]


def test_a_missing_route_table_or_non_literal_declaration_raises(tmp_path: Path) -> None:
    with pytest.raises(gi.InventoryError, match="no literal self._routes"):
        gi.discover(_fixture(tmp_path, "class App:\n    pass\n"))
    dynamic = APP.replace('"/v1/probe"', "PATH")
    with pytest.raises(gi.InventoryError, match="not a string literal"):
        gi.discover(_fixture(tmp_path, dynamic))
    with pytest.raises(gi.InventoryError, match="required source file not found"):
        gi.discover(tmp_path / "absent")


def test_report_shape_matches_the_other_domain_inventories() -> None:
    report = gi.discover_with_issues(gi.ROOT)
    assert report["schema_version"] == gi.SCHEMA_VERSION
    assert report["surfaces"] == gi.discover(gi.ROOT)
    assert report["issues"] == []
