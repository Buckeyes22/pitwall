"""The Python extractor reproduces the retired Node extractor byte for byte on a fixture."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from pitwall.gateway_catalog import extract

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "omniroute"
PACKAGE = FIXTURES / "package"


def test_python_extractor_matches_recorded_mjs_output() -> None:
    recorded = json.loads(gzip.decompress((FIXTURES / "expected_extract.json.gz").read_bytes()))
    actual = extract.extract_catalog(extract.read_package_dir(PACKAGE))
    assert actual == recorded
    assert json.dumps(actual, sort_keys=True) == json.dumps(recorded, sort_keys=True)
    assert len(actual["budgets"]) == 456
    assert "no-index-dir" not in actual["registry"]


def test_missing_source_file_is_rejected() -> None:
    with pytest.raises(ValueError, match="missing"):
        extract.extract_catalog({})


def test_wanted_paths_selects_only_the_sources_the_extractor_reads() -> None:
    paths = [
        extract.DATA_PATH,
        extract.ENDPOINTS_PATH,
        "open-sse/config/providers/registry/groq/index.ts",
        "open-sse/config/providers/registry/groq/models.ts",
        "open-sse/config/providers/registry/web/cn/index.ts",
        "README.md",
    ]
    assert extract.wanted_paths(paths) == paths[:3]
