from __future__ import annotations

import hashlib
import subprocess

from tools.security import check_secrets


def test_candidate_files_include_tracked_and_nonignored_untracked(monkeypatch) -> None:
    completed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=b"tracked.py\0new.py\0",
        stderr=b"",
    )
    monkeypatch.setattr(check_secrets.subprocess, "run", lambda *_args, **_kwargs: completed)

    assert check_secrets._candidate_files() == ["tracked.py", "new.py"]


def _scan_tree(tmp_path, files: dict[str, str]) -> set[tuple[str, str]]:
    """Run the gate's exact detect-secrets command over a scratch tree; return (file, type)."""
    import json
    import shutil

    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    shutil.copy2(check_secrets._BASELINE, baseline)
    baseline.write_text(
        json.dumps({**json.loads(baseline.read_text(encoding="utf-8")), "results": {}}),
        encoding="utf-8",
    )
    command = [shutil.which("detect-secrets") or "detect-secrets", "scan", "--no-verify"]
    command.extend(("--baseline", str(baseline)))
    for pattern in check_secrets._EXCLUDES[:1]:
        command.extend(("--exclude-files", pattern))
    for pattern in check_secrets._EXCLUDE_LINES:
        command.extend(("--exclude-lines", pattern))
    command.extend(files)
    subprocess.run(command, cwd=tmp_path, check=True, capture_output=True)
    results = json.loads(baseline.read_text(encoding="utf-8"))["results"]
    return {(name, item["type"]) for name, items in results.items() for item in items}


def test_digest_lines_are_not_findings_but_other_hex_still_is(tmp_path) -> None:
    digest = hashlib.sha256(b"planted digest").hexdigest()
    revision = hashlib.sha1(b"planted revision", usedforsecurity=False).hexdigest()
    found = _scan_tree(
        tmp_path,
        {
            "digest.json": f'{{\n  "test_source_sha256": "{digest}",\n  "sha256": "{digest}"\n}}\n',
            "revision.json": f'{{\n  "value": "{revision}",\n  "method": "extracted"\n}}\n',
            # The same strings on lines that are not a digest or revision field stay findings.
            "planted.py": f'token = "{digest}"\nother = "{revision}"\n',
            "planted_value.json": f'{{\n  "value": "{revision}00",\n  "k": "{digest}"\n}}\n',
        },
    )
    assert ("digest.json", "Hex High Entropy String") not in found
    assert ("revision.json", "Hex High Entropy String") not in found
    assert ("planted.py", "Hex High Entropy String") in found
    assert ("planted_value.json", "Hex High Entropy String") in found


def test_failure_messages_print_the_regeneration_command() -> None:
    assert "check_secrets.py --regenerate" in check_secrets._REGENERATE
    assert check_secrets._AUDIT.startswith("uv run --frozen detect-secrets audit")
