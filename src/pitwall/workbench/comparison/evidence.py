"""Locating and reading persisted child-session evidence on disk."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from pitwall.workbench.comparison._json import JsonObject, get_str, parse_json_object
from pitwall.workbench.comparison.child_evidence import (
    ChildReceipt,
    ChildSessionTiming,
    child_session_header_matches,
    child_session_proves_read,
    child_session_thinking_levels,
    child_session_timing,
    child_session_usage,
    select_single_child_evidence_path,
)
from pitwall.workbench.comparison.metrics import UsageTotals, add_usage, empty_usage


@dataclass(frozen=True)
class PersistedChildEvidence:
    correlated: bool
    files: list[str]
    child_thinking_levels: list[str]
    child_usage: UsageTotals
    session_timing: ChildSessionTiming | None
    reason: str | None = None

    def to_json(self) -> JsonObject:
        return {
            "correlated": self.correlated,
            "files": list(self.files),
            "sessionTiming": self.session_timing.to_json() if self.session_timing else None,
        }


def resolve_path(base: Path | str, target: Path | str) -> str:
    """``target`` resolved against ``base`` like Node's ``path.resolve``."""
    return os.path.abspath(os.path.join(base, target))


def jsonl_files(root: Path) -> list[Path]:
    """Every ``.jsonl`` file below ``root`` in a stable order."""
    return sorted(path for path in root.rglob("*.jsonl") if path.is_file()) if root.is_dir() else []


def files_containing_child_evidence(
    root: Path, child_id: str, expected_parent_session: str | None = None
) -> list[str]:
    """Relative paths of sessions whose typed header binds ``child_id`` to the exact parent."""
    found: list[str] = []
    for path in jsonl_files(root):
        try:
            content = path.read_text()
        except OSError:
            # Evidence is best effort and remains unexecuted.
            continue
        # A sibling session may share the parent session and display prefix; require a typed header
        # identity plus the exact parent link. A file name containing the child id alone is not a
        # provenance binding.
        if child_session_header_matches(content, child_id, expected_parent_session):
            found.append(path.relative_to(root).as_posix())
    return found


def find_parent_session_file(agent_dir: Path, cwd: Path | str) -> str | None:
    """The session file of the top-level Pi session for ``cwd`` (no ``parentSession`` link)."""
    for path in jsonl_files(agent_dir):
        try:
            first = path.read_text().split("\n", 1)[0]
        except OSError:
            # Unreadable records are not parent identity.
            continue
        header = parse_json_object(first)
        if (
            header is not None
            and header.get("type") == "session"
            and header.get("cwd") == str(cwd)
            and not isinstance(header.get("parentSession"), str)
        ):
            return str(path)
    return None


def persisted_child_evidence(
    agent_dir: Path,
    cwd: Path,
    marker: str,
    receipt: ChildReceipt,
    parent_session_file: str | None = None,
) -> PersistedChildEvidence:
    """Correlate a completed child receipt to its persisted session and read its evidence."""
    child_id = receipt.child_id
    if not child_id:
        return PersistedChildEvidence(
            False,
            [],
            [],
            empty_usage(),
            None,
            "completed child result did not expose a child run identifier",
        )
    files: list[str] = []
    recorded: set[str] = set()
    levels: list[str] = []
    usage = empty_usage()
    timing: ChildSessionTiming | None = None
    parent_absolute = resolve_path(cwd, parent_session_file) if parent_session_file else None

    def record(path: str, content: str) -> None:
        nonlocal usage, timing
        if path in recorded:
            return
        recorded.add(path)
        for level in child_session_thinking_levels(content):
            if level not in levels:
                levels.append(level)
        usage = add_usage(usage, child_session_usage(content))
        timing = child_session_timing(content)

    if receipt.session_file:
        try:
            session_path = resolve_path(cwd, receipt.session_file)
            content = Path(session_path).read_text()
            # The backend-provided path is authoritative only when its typed header binds the
            # expected child to the exact parent, the same check the fallback scan applies.
            if parent_absolute is None:
                return PersistedChildEvidence(
                    False,
                    [],
                    [],
                    empty_usage(),
                    None,
                    "no parent session path is known to validate the child session",
                )
            if not child_session_header_matches(content, child_id, parent_absolute):
                return PersistedChildEvidence(
                    False,
                    [],
                    [],
                    empty_usage(),
                    None,
                    "authoritative child session header does not name the expected child and parent",
                )
            record(session_path, content)
            if session_path != (parent_absolute or "") and child_session_proves_read(
                content, marker
            ):
                files.append(session_path)
            # The backend-provided session path is authoritative. Do not combine it with a sibling
            # artifact that happens to share a parent or marker.
            unique = list(dict.fromkeys(files))
            if unique:
                return PersistedChildEvidence(True, unique, list(levels), usage, timing)
            return PersistedChildEvidence(
                False,
                unique,
                list(levels),
                usage,
                timing,
                "authoritative child session did not contain the fixture marker",
            )
        except OSError:
            return PersistedChildEvidence(
                False,
                [],
                [],
                empty_usage(),
                None,
                "authoritative child session was unavailable",
            )
    selected = select_single_child_evidence_path(
        files_containing_child_evidence(agent_dir, child_id, parent_absolute)
    )
    if selected is None:
        return PersistedChildEvidence(
            False, [], [], usage, None, "no unique child session identity was found"
        )
    candidate = str(agent_dir / selected)
    try:
        content = Path(candidate).read_text()
        record(candidate, content)
        if child_session_proves_read(content, marker):
            files.append(candidate)
    except OSError:
        # Unavailable evidence remains unexecuted.
        pass
    unique = list(dict.fromkeys(files))
    if unique:
        return PersistedChildEvidence(True, unique, list(levels), usage, timing)
    return PersistedChildEvidence(
        False,
        unique,
        list(levels),
        usage,
        timing,
        "child identifier was exposed but no matching persisted child session contained the fixture marker",
    )


def session_header(content: str) -> JsonObject | None:
    """The first record of a persisted session when it is a JSON object."""
    first = next((line for line in content.split("\n") if line.strip()), None)
    return parse_json_object(first) if first is not None else None


def session_cwd(content: str) -> str | None:
    return get_str(session_header(content), "cwd")
