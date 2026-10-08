"""Deterministic candidate identity snapshot for release acceptance.

``build_candidate(root, output=None, artifacts=None)`` returns one
JSON-serializable record whose ``candidate_id`` is a content hash of everything
the candidate owns: HEAD, the listed file manifest (mode and content, including
binaries, symlink targets, and gitlinks), required dependency locks, and the
declared artifact digests. ``generated_at``, the ``--output`` path, the absolute
checkout location, and volatile git status details are recorded but excluded
from the identity, so regenerating the snapshot never invalidates it.

File-set authority is ``git ls-files --cached --others --exclude-standard -z``
plus ``git ls-files --stage -z`` for indexed modes and gitlink commits. This
module never walks the tree, never reads bytes through a path that resolves
outside the repository root, and never reads gitignored files. It is read-only
and performs no network access. ``release_ready`` flags
source identity only; it is not a global claim that a release may ship.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "release-acceptance-candidate.v1"
CHUNK = 1 << 20
GIT_TIMEOUT_S = 30
PROBE_TIMEOUT_S = 10
REDACTED = "REDACTED"

GENERATED_NAMES = frozenset({".venv", "node_modules", "dist", "build", "__pycache__"})
OUTSIDE_ROOT_ISSUE = "path resolves outside repository root; content not read"
GENERATED_RELATIVE = (
    "mutants/cache",
    "release_acceptance/runs",
    "release_acceptance/generated",
    "release_acceptance/final-packet",
)


class CandidateError(RuntimeError):
    """Git or tool probe failure with a diagnostic message."""


def _run(args: list[str], cwd: Path | None, timeout: int) -> bytes:
    try:
        done = subprocess.run(
            args,
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CandidateError(f"{args[0]} probe failed: {exc}") from exc
    if done.returncode != 0:
        detail = done.stderr.decode("utf-8", "replace").strip() or f"exit {done.returncode}"
        raise CandidateError(f"{' '.join(args)} failed: {detail}")
    return done.stdout


def _git(root: Path, *args: str) -> str:
    return _run(["git", "-C", str(root), *args], root, GIT_TIMEOUT_S).decode("utf-8", "replace")


def _redact(url: str) -> str:
    """Return a remote URL safe for metadata: drop userinfo, query, and fragment."""
    metadata = url
    fragment = ""
    if "#" in metadata:
        metadata, fragment = metadata.split("#", 1)
    query_split = metadata.split("?", 1)
    query = query_split[1] if len(query_split) == 2 else None
    if query is not None:
        metadata = query_split[0]
    if "://" in metadata:
        scheme, rest = metadata.split("://", 1)
        if "@" in rest:
            rest = rest.split("@", 1)[1]
            redacted = f"{scheme}://{REDACTED}@{rest}"
        else:
            redacted = f"{scheme}://{rest}"
    elif "@" in metadata:
        redacted = f"{REDACTED}@{metadata.split('@', 1)[1]}"
    else:
        redacted = metadata
    if query is not None or fragment:
        redacted += "#" + REDACTED
    return redacted


def _git_state(root: Path) -> dict[str, Any]:
    state: dict[str, Any] = {
        "commit": None,
        "branch": None,
        "merge_base": None,
        "remote": None,
        "detached": False,
        "error": None,
    }
    try:
        state["commit"] = _git(root, "rev-parse", "HEAD").strip() or None
        branch = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD").strip()
        state["branch"] = branch or None
        state["detached"] = not branch
        for line in _run(
            ["git", "-C", str(root), "remote", "-v"], root, GIT_TIMEOUT_S
        ).splitlines():
            fields = line.decode("utf-8", "replace").split()
            if len(fields) >= 2 and (state["remote"] is None or fields[0] == "origin"):
                state["remote"] = _redact(fields[1])
        for name, ref in (("origin/HEAD", "origin/HEAD"), ("main", "refs/heads/main")):
            code = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", ref],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            ).returncode
            if code == 0:
                state["merge_base"] = _git(root, "merge-base", "HEAD", name).strip() or None
                break
    except CandidateError as exc:
        state["error"] = str(exc)
    return state


def _status_path(entry: str) -> str:
    if entry.startswith("? ") or entry.startswith("! "):
        return entry[2:]
    index = {"1": 8, "2": 8, "u": 10}.get(entry[:1])
    if index is None:
        return entry
    fields = entry.split(" ", index)
    path = fields[index] if len(fields) > index else entry
    return path.split("\t", 1)[0]


def _git_status(root: Path) -> dict[str, Any]:
    raw = _run(
        ["git", "-C", str(root), "status", "--porcelain=v2", "-z", "--untracked-files=all"],
        root,
        GIT_TIMEOUT_S,
    )
    parts = [part for part in raw.decode("utf-8", "surrogateescape").split("\0") if part]
    paths = sorted({_status_path(part) for part in parts})
    status: dict[str, Any] = {"clean": not paths, "entry_count": len(paths), "paths": paths}
    if not paths:
        try:
            status["stash_count"] = int(
                _git(root, "rev-list", "--walk-reflogs", "--count", "refs/stash").strip()
            )
        except CandidateError:
            status["stash_count"] = 0
    return status


def _listed_paths(root: Path) -> list[str]:
    raw = _run(
        ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        root,
        GIT_TIMEOUT_S,
    )
    return [name.decode("utf-8", "surrogateescape") for name in raw.split(b"\0") if name]


def _index_entries(root: Path) -> dict[str, tuple[str, str]]:
    entries: dict[str, tuple[str, str]] = {}
    for item in _run(
        ["git", "-C", str(root), "ls-files", "--stage", "-z"], root, GIT_TIMEOUT_S
    ).split(b"\0"):
        if not item:
            continue
        meta, _, name = item.partition(b"\t")
        fields = meta.split(b" ")
        if len(fields) >= 2:
            entries[name.decode("utf-8", "surrogateescape")] = (
                fields[0].decode("ascii"),
                fields[1].decode("ascii"),
            )
    return entries


def _generated_reason(relative: str) -> str | None:
    parts = relative.split("/")
    for part in parts:
        if part in GENERATED_NAMES:
            return f"generated:{part}"
    for known in GENERATED_RELATIVE:
        if relative == known or relative.startswith(known + "/") or f"/{known}/" in f"/{relative}/":
            return f"generated:{known}"
    return None


def _within_root(path: Path, root: Path) -> bool:
    """True only when ``path`` resolves to ``root`` or a descendant of it."""
    try:
        resolved = path.resolve()
    except OSError:
        return False
    return resolved == root or resolved.is_relative_to(root)


def _sha256_file(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(CHUNK):
                digest.update(chunk)
    except OSError, MemoryError:
        return None
    return digest.hexdigest()


def _build_manifest(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    indexed = _index_entries(root)
    manifest: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    for relative in _listed_paths(root):
        reason = _generated_reason(relative)
        if reason is not None:
            excluded.append({"path": relative, "reason": reason})
            continue
        path = root / relative
        index_mode, index_object = indexed.get(relative, (None, None))
        record: dict[str, Any] = {"path": relative, "sha256": None}
        if index_mode is not None:
            record["index_mode"] = index_mode
        if index_mode == "160000":
            record["mode"] = "gitlink"
            record["mode160000"] = True
            record["git_commit"] = index_object
            record["issue"] = "gitlink component source not read; external proof required"
        elif path.is_symlink():
            # Only the literal target is read; the symlink is never followed.
            target = os.readlink(path)
            record["mode"] = "symlink"
            record["symlink_target"] = target
            record["sha256"] = hashlib.sha256(target.encode("utf-8", "surrogateescape")).hexdigest()
        elif not _within_root(path, root):
            # A parent directory symlink escapes the root: decide containment on
            # the resolved parent before any is_file/lstat/hash read below.
            record["mode"] = "blocked"
            record["issue"] = OUTSIDE_ROOT_ISSUE
        else:
            try:
                stat_result = path.lstat()
            except OSError:
                record["mode"] = "missing"
            else:
                if not path.is_file():
                    record["mode"] = "other"
                    record["issue"] = "not a regular file; content not hashed"
                else:
                    record["mode"] = "executable" if stat_result.st_mode & 0o111 else "file"
                    record["sha256"] = _sha256_file(path)
                    if record["sha256"] is None:
                        record["issue"] = "unreadable"
        manifest.append(record)
    return manifest, excluded


def _artifact_record(name: str, raw: str, root: Path) -> dict[str, Any]:
    path = Path(raw)
    if not path.is_absolute():
        path = root / raw
    record: dict[str, Any] = {"name": name, "path": raw, "sha256": _sha256_file(path)}
    if record["sha256"] is None:
        record["error"] = "unreadable or outside repository bytes unavailable"
    return record


def _dep_record(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    is_link = path.is_symlink()
    contained = _within_root(path, root)
    # Never stat or read through a path that resolves outside the root; a
    # symlinked lock counts as missing so release_ready stays false.
    present = path.is_file() if contained else False
    record: dict[str, Any] = {"path": relative, "present": present, "sha256": None}
    if present:
        record["sha256"] = _sha256_file(path)
    elif not contained:
        target = os.readlink(path) if is_link else str(path)
        record["issue"] = f"lock resolves outside repository root ({target}); not read"
    elif is_link:
        record["issue"] = "broken lock symlink; not read"
    return record


def _required_locks(root: Path) -> tuple[str, ...]:
    return ("uv.lock",)


def _tool_versions() -> dict[str, Any]:
    versions: dict[str, Any] = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "node": None,
        "uv": None,
    }
    for name in ("node", "uv"):
        try:
            versions[name] = (
                _run([name, "--version"], None, PROBE_TIMEOUT_S).decode("utf-8", "replace").strip()
                or None
            )
        except CandidateError:
            versions[name] = None
    return versions


def _identity(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": record["schema_version"],
        "git": {"commit": record["git"]["commit"], "merge_base": record["git"]["merge_base"]},
        "manifest": [
            {key: value for key, value in item.items() if key != "issue"}
            for item in record["manifest"]
        ],
        "dependencies": record["dependencies"],
        "artifacts": [
            {"name": item["name"], "sha256": item["sha256"]} for item in record["artifacts"]
        ],
    }


def build_candidate(
    root: Path,
    output: Path | None = None,
    artifacts: dict[str, str] | None = None,
) -> dict[str, Any]:
    root = Path(root).resolve()
    artifacts = dict(artifacts or {})
    output_relative: str | None = None
    if output is not None:
        candidate = Path(output) if Path(output).is_absolute() else root / output
        try:
            output_relative = candidate.resolve().relative_to(root).as_posix()
        except ValueError:
            output_relative = None

    list_error: str | None = None
    try:
        manifest, excluded = _build_manifest(root)
    except CandidateError as exc:
        manifest, excluded = [], []
        list_error = str(exc)
    artifact_records = [
        _artifact_record(name, raw, root) for name, raw in sorted(artifacts.items())
    ]
    artifact_relatives: set[str] = set()
    for raw in artifacts.values():
        artifact_path = Path(raw)
        if not artifact_path.is_absolute():
            artifact_path = root / raw
        try:
            artifact_relatives.add(artifact_path.resolve().relative_to(root).as_posix())
        except ValueError:
            continue
    drop = {output_relative} if output_relative else set()
    drop.update(artifact_relatives)
    if output_relative:
        excluded.append({"path": output_relative, "reason": "output"})
    for relative in sorted(artifact_relatives):
        excluded.append({"path": relative, "reason": "artifact"})
    manifest = [item for item in manifest if item["path"] not in drop]

    dependencies = [_dep_record(root, relative) for relative in _required_locks(root)]
    missing = [item["path"] for item in dependencies if not item["present"]]
    git = _git_state(root)
    try:
        status = _git_status(root)
    except CandidateError as exc:
        status = {"clean": None, "entry_count": None, "paths": [], "error": str(exc)}

    issues: list[str] = []
    if missing:
        issues.append("missing required lock(s): " + ", ".join(missing))
    for item in dependencies:
        if item.get("issue"):
            issues.append(f"{item['path']}: {item['issue']}")
    if list_error:
        issues.append(f"file list unavailable: {list_error}")
    if status["clean"] is not True:
        issues.append("work tree not clean or unknowable; snapshot is exploratory")
    if git["error"]:
        issues.append(f"git metadata error: {git['error']}")
    for item in manifest:
        if item.get("issue"):
            issues.append(f"{item['path']}: {item['issue']}")
    unreadable = [
        item["path"] for item in manifest if item["sha256"] is None and item["mode"] != "missing"
    ]
    unreadable.extend(item["name"] for item in artifact_records if item["sha256"] is None)
    if unreadable:
        issues.append("unreadable or unproven input(s): " + ", ".join(sorted(unreadable)))

    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "candidate_id": None,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "root": str(root),
        "scope": "source identity only; not a global release-readiness claim",
        "git": git,
        "git_status": status,
        "clean": status["clean"] is True,
        "dirty": status["clean"] is False,
        "release_ready": bool(
            git["commit"] is not None
            and git["error"] is None
            and list_error is None
            and status["clean"] is True
            and not missing
            and not unreadable
        ),
        "manifest": manifest,
        "excluded": excluded,
        "dependencies": dependencies,
        "artifacts": artifact_records,
        "tools": _tool_versions(),
        "issues": sorted(set(issues)),
    }
    canonical = json.dumps(
        _identity(record), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    record["candidate_id"] = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    if output is not None:
        target = Path(output) if Path(output).is_absolute() else root / output
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record


def _parse_artifacts(values: list[str]) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    for value in values:
        name, sep, path = value.partition("=")
        if not sep or not name or not path:
            raise ValueError(f"artifact {value!r} must be NAME=PATH")
        artifacts[name] = path
    return artifacts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="candidate")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--artifact", action="append", default=[], metavar="NAME=PATH")
    args = parser.parse_args(argv)
    try:
        record = build_candidate(
            args.root, output=args.output, artifacts=_parse_artifacts(args.artifact)
        )
    except (CandidateError, ValueError) as exc:
        print(f"candidate: {exc}", file=sys.stderr)
        return 1
    if args.output is None:
        print(json.dumps(record, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
