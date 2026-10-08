"""Write a bounded, source-only pytest collection receipt.

The plugin is deliberately usable only with ``--collect-only``.  It records
the items pytest actually collected, their candidate-relative source file
digests and locations, and safe collection failure/skip summaries.  It never
executes a test body and writes one fresh private receipt at the caller's
explicit path.

Example::

    .venv/bin/python -m pytest --collect-only -p tools.release_acceptance.pytest_collection \
      --pitwall-collection-root /path/to/candidate \
      --pitwall-collection-output /private/receipt/pytest-collection.json \
      tests/test_example.py

The output path must be outside the candidate root and must not already exist;
the plugin uses an exclusive 0600 create and never follows a symlink.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest

_STATE_KEY = pytest.StashKey[dict[str, Any]]()

SCHEMA_VERSION = "release-acceptance-pytest-collection.v1"
_ACTIVE_STATE: dict[str, Any] | None = None


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _absolute(path: Path) -> Path:
    return path.expanduser() if path.is_absolute() else Path.cwd() / path


def _outside(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return True
    return False


def _relative_source(
    root: Path, item: pytest.Item
) -> tuple[str | None, str | None, int | None, str | None]:
    """Return safe source metadata, without reading outside ``root``."""
    raw_path = getattr(item, "path", None)
    if raw_path is None:
        raw_path = getattr(item, "fspath", None)
    if raw_path is None:
        return None, None, None, "pytest item has no source path"
    try:
        source = Path(str(raw_path)).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        return None, None, None, f"item source is unavailable: {type(exc).__name__}"
    if _outside(root, source):
        return None, None, None, "item source resolves outside the candidate root"
    try:
        payload = source.read_bytes()
    except (OSError, UnicodeError) as exc:
        return None, None, None, f"item source could not be read: {type(exc).__name__}"
    location = getattr(item, "location", None)
    line = (
        location[1] + 1
        if isinstance(location, tuple) and len(location) > 1 and isinstance(location[1], int)
        else None
    )
    return source.relative_to(root).as_posix(), _sha256(payload), line, None


def _function_name(nodeid: str) -> str:
    """Remove parameter IDs while retaining pytest class/function identity."""
    suffix = nodeid.split("::", 1)[1] if "::" in nodeid else nodeid
    return "::".join(part.split("[", 1)[0] for part in suffix.split("::"))


def _item_row(root: Path, item: pytest.Item) -> tuple[dict[str, Any], str | None]:
    nodeid = str(getattr(item, "nodeid", ""))
    source_file, source_sha256, line, issue = _relative_source(root, item)
    row: dict[str, Any] = {
        "collected_id": nodeid,
        "nodeid": nodeid,
        "framework": "pytest",
        "source_file": source_file,
        "source_sha256": source_sha256,
        "line": line,
        "function": _function_name(nodeid),
        "parameterized": hasattr(item, "callspec"),
    }
    return row, issue


def _write_receipt(path: Path, payload: dict[str, Any]) -> None:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise RuntimeError("collection output is not a regular file")
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = None
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except FileExistsError as exc:
        raise RuntimeError("collection output must be a new path") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("pitwall release acceptance")
    group.addoption(
        "--pitwall-collection-root",
        action="store",
        required=True,
        help="candidate root whose source files may be read",
    )
    group.addoption(
        "--pitwall-collection-output",
        action="store",
        required=True,
        help="new external JSON receipt path",
    )


def pytest_configure(config: pytest.Config) -> None:
    global _ACTIVE_STATE
    if not bool(config.getoption("collectonly")):
        raise pytest.UsageError("pitwall collection receipts require pytest --collect-only")
    root_arg = config.getoption("pitwall_collection_root")
    output_arg = config.getoption("pitwall_collection_output")
    root = Path(str(root_arg)).expanduser().resolve(strict=True)
    output_requested = _absolute(Path(str(output_arg)))
    output = output_requested.resolve()
    if not _outside(root, output):
        raise pytest.UsageError("pitwall collection output must resolve outside the candidate root")
    if output.exists() or output.is_symlink():
        raise pytest.UsageError("pitwall collection output must be a new path")
    if not output.parent.is_dir():
        raise pytest.UsageError("pitwall collection output parent must already exist")
    state = {
        "root": root,
        "output": output,
        "items": [],
        "issues": [],
        "failures": [],
        "skips": [],
    }
    config.stash[_STATE_KEY] = state
    _ACTIVE_STATE = state


def pytest_collection_modifyitems(
    session: pytest.Session, config: pytest.Config, items: list[pytest.Item]
) -> None:
    state = config.stash[_STATE_KEY]
    for item in items:
        row, issue = _item_row(state["root"], item)
        state["items"].append(row)
        if issue is not None:
            state["issues"].append(
                {"kind": "unsafe-item-source", "nodeid": row["nodeid"], "reason": issue}
            )


def pytest_collectreport(report: pytest.CollectReport) -> None:
    # CollectReport does not consistently expose its Config across supported
    # pytest versions, so use the one active state for this process.
    state = _ACTIVE_STATE
    if state is None or report.outcome not in {"failed", "skipped"}:
        return
    row = {"nodeid": str(getattr(report, "nodeid", "")), "outcome": str(report.outcome)}
    (state["failures"] if report.failed else state["skips"]).append(row)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    config = session.config
    state = config.stash.get(_STATE_KEY, None)
    if state is None:
        return
    # Some pytest versions do not attach config to CollectReport.  The item
    # list still gives a reliable receipt, while collection failures are
    # represented by the session exit status and the safe issue list below.
    items = sorted(
        state["items"], key=lambda row: (str(row.get("collected_id")), str(row.get("source_file")))
    )
    for key in ("issues", "failures", "skips"):
        state[key] = sorted(state[key], key=lambda row: json.dumps(row, sort_keys=True))
    report = {
        "schema_version": SCHEMA_VERSION,
        "candidate_root": str(state["root"]),
        "pytest_root": str(Path(str(config.rootpath)).resolve()),
        "pytest_version": pytest.__version__,
        "collect_only": bool(config.getoption("collectonly")),
        "exitstatus": int(exitstatus),
        "status": "complete"
        if exitstatus == 0 and not state["issues"] and not state["failures"]
        else "unresolved",
        "items": items,
        "collection_failures": state["failures"],
        "collection_skips": state["skips"],
        "issues": state["issues"],
    }
    _write_receipt(state["output"], report)
