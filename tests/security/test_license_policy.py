"""License policy rejects unknown, denied, and drifted reviewed licenses."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def _module() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "tools" / "security" / "check_licenses.py"
    spec = importlib.util.spec_from_file_location("check_licenses", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _policy() -> dict[str, object]:
    return {
        "allowed_license_terms": ["MIT", "Apache-2.0"],
        "denied_license_terms": ["AGPL"],
        "review_required_packages": {"special": {"version": "2", "license": "MPL-2.0"}},
    }


def test_policy_accepts_allowlist_and_exact_review() -> None:
    rows = [
        {"name": "normal", "version": "1", "license": "MIT"},
        {"name": "special", "version": "2", "license": "MPL-2.0"},
    ]
    assert _module().evaluate(rows, _policy()) == []


def test_policy_rejects_unknown_denied_and_review_drift() -> None:
    rows = [
        {"name": "unknown", "version": "1", "license": "UNKNOWN"},
        {"name": "denied", "version": "1", "license": "AGPL-3.0"},
        {"name": "special", "version": "2", "license": "MPL-2.0+"},
    ]
    errors = _module().evaluate(rows, _policy())
    assert len(errors) == 3


def test_policy_rejects_reviewed_package_version_drift() -> None:
    rows = [{"name": "special", "version": "3", "license": "MPL-2.0"}]
    errors = _module().evaluate(rows, _policy())
    assert errors == ["special: version changed from reviewed '2' to '3'"]


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
    rows = [
        {"name": "rfc3987", "version": "1.3.8", "license": "GNU GPLv3+"},
        {"name": "special", "version": "2", "license": "MPL-2.0"},
    ]
    assert _module().evaluate(rows, policy, profile="dev") == []
    assert _module().evaluate(rows, policy, profile="base") == [
        "rfc3987 1.3.8: denied license 'GNU GPLv3+'"
    ]


def test_npm_lock_rows_read_name_version_license() -> None:
    lock = {
        "packages": {
            "": {"name": "root"},
            "node_modules/@scope/a": {"version": "1.0.0", "license": "MIT"},
            "node_modules/b/node_modules/c": {"version": "2.0.0"},
        }
    }
    assert _module().npm_lock_rows(lock) == [
        {"name": "@scope/a", "version": "1.0.0", "license": "MIT"},
        {"name": "c", "version": "2.0.0", "license": "UNKNOWN"},
    ]


def test_npm_reviews_are_keyed_by_version_and_accept_the_verified_license() -> None:
    policy = _policy() | {
        "profile_review_required": {
            "npm": {
                "dup@1.0.0": {
                    "version": "1.0.0",
                    "license": "UNKNOWN",
                    "verified_license": "MIT",
                }
            }
        }
    }
    rows = [
        {"name": "dup", "version": "1.0.0", "license": "UNKNOWN"},
        {"name": "dup", "version": "1.0.0", "license": "MIT"},
    ]
    assert _module().evaluate(rows, policy, profile="npm") == []
    drift = [{"name": "dup", "version": "1.0.1", "license": "UNKNOWN"}]
    assert _module().evaluate(drift, policy, profile="npm") == [
        "dup@1.0.1 1.0.1: unknown or unapproved license 'UNKNOWN'",
        "review-required package missing from npm graph: dup@1.0.0",
    ]


def test_a_missing_extra_is_one_line_naming_the_sync_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _module()

    def missing(_root: str, _extras: frozenset[str]) -> list[object]:
        raise RuntimeError("dependency is not installed: resend")

    monkeypatch.setattr(module, "runtime_graph", missing)
    monkeypatch.setattr(module.sys, "argv", ["check_licenses.py", "--extra", "email"])
    assert module.main() == 2
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "resend" in err and "uv sync --frozen --all-extras" in err
