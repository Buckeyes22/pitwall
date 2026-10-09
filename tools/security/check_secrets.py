"""Deterministic, repository-wide detect-secrets policy gate.

The upstream CLI rewrites timestamps and line numbers in a baseline, which made
the former CI diff both noisy and scope-dependent. This gate compares semantic
fingerprints instead: every current finding must have an explicit false-positive
decision in the committed baseline, and stale decisions must be removed. Tests
and fixtures are intentionally included.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, cast

_ROOT = Path(__file__).resolve().parents[2]
_BASELINE = _ROOT / ".secrets.baseline"
_EXCLUDES = (
    r"(?:^|/)\.git/",
    r"(?:^|/)\.venv/",
    r"(?:^|/)uv\.lock$",
    r"(?:^|/)\.secrets\.baseline$",
)
# Lines whose only high-entropy content is a content digest or a pinned revision. They change
# every time a bound test file or model-facts revision changes, are public hashes rather than
# credentials, and would otherwise force a second baseline regeneration after every edit.
# Everything else in those files, and every other line, is still scanned.
_EXCLUDE_LINES = (
    # release_acceptance records and installer pins: "sha256": "<64 hex>" (and *_sha256 keys)
    r'sha256": "[0-9a-f]{64}"',
    # model-facts revision fact: the whole line is `"value": "<40 hex git revision>"`
    r'^\s*"value": "[0-9a-f]{40}",?\s*$',
)
_REGENERATE = "uv run --frozen python tools/security/check_secrets.py --regenerate"
_AUDIT = "uv run --frozen detect-secrets audit .secrets.baseline"


def _fingerprints(document: dict[str, Any]) -> set[tuple[str, str, str]]:
    return {
        (filename, str(item["type"]), str(item["hashed_secret"]))
        for filename, items in document.get("results", {}).items()
        for item in items
    }


def _unreviewed(document: dict[str, Any]) -> list[tuple[str, int, str]]:
    return [
        (filename, int(item.get("line_number", 0)), str(item["type"]))
        for filename, items in document.get("results", {}).items()
        for item in items
        if item.get("is_secret") is not False
    ]


def _candidate_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=_ROOT,
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        message = os.fsdecode(result.stderr).strip()
        raise RuntimeError(message or "git file inventory failed")
    return [os.fsdecode(path) for path in result.stdout.split(b"\0") if path]


def _scan(baseline: Path) -> dict[str, Any]:
    if importlib.util.find_spec("detect_secrets") is None:
        raise RuntimeError("detect-secrets is not installed; run `uv sync --frozen --extra dev`")
    # The scanner runs under this interpreter, so the gate works without the venv on PATH.
    command = [sys.executable, "-m", "detect_secrets", "scan", "--no-verify"]
    command.extend(("--baseline", str(baseline)))
    for pattern in _EXCLUDES:
        command.extend(("--exclude-files", pattern))
    for pattern in _EXCLUDE_LINES:
        command.extend(("--exclude-lines", pattern))
    command.extend(_candidate_files())
    result = subprocess.run(command, cwd=_ROOT, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "detect-secrets scan failed")
    return cast(dict[str, Any], json.loads(baseline.read_text(encoding="utf-8")))


def _regenerate() -> int:
    """Rewrite the baseline from the canonical scan, keeping every existing audit decision."""
    with tempfile.TemporaryDirectory(prefix="pitwall-secret-scan-") as temp_dir:
        scan_baseline = Path(temp_dir) / "baseline.json"
        shutil.copy2(_BASELINE, scan_baseline)
        document = _scan(scan_baseline)
    # The scan records its temporary copy as the baseline file; store the real name so a
    # regeneration with no new findings does not change the committed file.
    for entry in document.get("filters_used", []):
        if entry.get("path") == "detect_secrets.filters.common.is_baseline_file":
            entry["filename"] = _BASELINE.name
    _BASELINE.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    pending = _unreviewed(document)
    for filename, line, kind in pending:
        print(f"needs audit: {filename}:{line}: {kind}", file=sys.stderr)
    if pending:
        print(
            f"audit each finding above (mark real secrets for removal, false positives "
            f"is_secret=false) with `{_AUDIT}`, then rerun "
            "`uv run --frozen python tools/security/check_secrets.py`",
            file=sys.stderr,
        )
        return 1
    print(f"baseline regenerated: {len(_fingerprints(document))} reviewed findings")
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments == ["--regenerate"]:
        return _regenerate()
    if arguments:
        print("usage: check_secrets.py [--regenerate]", file=sys.stderr)
        return 2
    approved = json.loads(_BASELINE.read_text(encoding="utf-8"))
    pending = _unreviewed(approved)
    if pending:
        for filename, line, kind in pending:
            print(f"unreviewed baseline finding: {filename}:{line}: {kind}", file=sys.stderr)
        print(f"audit them with `{_AUDIT}`", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="pitwall-secret-scan-") as temp_dir:
        scan_baseline = Path(temp_dir) / "baseline.json"
        shutil.copy2(_BASELINE, scan_baseline)
        current = _scan(scan_baseline)

    approved_keys = _fingerprints(approved)
    current_keys = _fingerprints(current)
    additions = sorted(current_keys - approved_keys)
    stale = sorted(approved_keys - current_keys)
    for filename, kind, _digest in additions:
        print(f"new potential secret: {filename}: {kind}", file=sys.stderr)
    for filename, kind, _digest in stale:
        print(f"stale secret-baseline entry: {filename}: {kind}", file=sys.stderr)
    if additions or stale:
        print(
            f"fix: run `{_REGENERATE}`, audit any finding it lists with `{_AUDIT}`, and "
            "commit .secrets.baseline (a real secret must be removed from the tree instead)",
            file=sys.stderr,
        )
        return 1
    print(f"secret scan passed: {len(current_keys)} reviewed findings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
