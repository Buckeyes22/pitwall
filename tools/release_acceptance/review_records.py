"""Keep the source-review records current without a human edit for formatting-only changes.

``release_acceptance/discovery-review.json`` holds one reviewed entry per open discovery issue,
keyed by ``file:line``; ``release_acceptance/cli-source-review.json`` pins the files its
review read. Both used to pin whole-file SHA-256 values, so one comment line anywhere in a
reviewed file invalidated every entry for it with no tool to refresh them.

Now an entry pins the normalized AST of the reviewed definition only (``span_sha256``): the
innermost statement at the issue's line plus the headers of the loops, branches, classes,
and functions around it. Comments, blank lines, formatting, and edits elsewhere in the file
do not change it. Non-Python sources (compose files) pin the whitespace-normalized line.
Dependency pins (``normalized_sha256``) hash the whole module's AST for the same reason.

This command re-lines entries and re-hashes pins whose reviewed content is unchanged. When a
reviewed definition changed it writes nothing and lists the entries a human must re-read;
after that re-read, ``--accept-reviewed`` records the new content.

    uv run --frozen python -m tools.release_acceptance.review_records [--accept-reviewed]
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DISCOVERY_REVIEW = "release_acceptance/discovery-review.json"
DEPENDENCY_RECORDS = ("release_acceptance/cli-source-review.json",)
REFRESH_COMMAND = "uv run --frozen python -m tools.release_acceptance.review_records"
REFRESH_HINT = f"run `{REFRESH_COMMAND}` (or `make regen-bindings`)"
ACCEPT_HINT = f"after re-reading the listed definitions run `{REFRESH_COMMAND} --accept-reviewed`"

_BODY_FIELDS = ("body", "orelse", "finalbody", "handlers", "cases")


def _digest(parts: list[str]) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def _header(node: ast.AST) -> str:
    """Dump a compound statement without its nested statements."""
    shallow = copy.copy(node)
    for field in _BODY_FIELDS:
        if hasattr(shallow, field):
            setattr(shallow, field, [])
    return ast.dump(shallow)


def _chain(parent: ast.AST, line: int) -> list[ast.AST]:
    """Return the statements enclosing ``line``, outermost first."""
    for child in ast.iter_child_nodes(parent):
        if isinstance(child, ast.stmt | ast.ExceptHandler):
            if child.lineno <= line <= (child.end_lineno or child.lineno):
                return [child, *_chain(child, line)]
        elif isinstance(child, ast.match_case):
            nested = _chain(child, line)
            if nested:
                return nested
    return []


def span_digest(root: Path, source: str) -> str:
    """Digest the reviewed definition at ``path:line``, ignoring comments and layout."""
    relative, _, line_text = source.rpartition(":")
    path = root / relative
    text = path.read_text(encoding="utf-8")
    line = int(line_text)
    if path.suffix == ".py":
        try:
            tree = ast.parse(text)
        except SyntaxError:
            tree = None
        chain = _chain(tree, line) if tree is not None else []
        if chain:
            parts = [relative]
            for outer in chain[:-1]:
                parts.append(
                    f"{type(outer).__name__}:{outer.name}"  # type: ignore[attr-defined]
                    if isinstance(outer, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
                    else _header(outer)
                )
            parts.append(_header(chain[-1]))
            return _digest(parts)
    lines = text.splitlines()
    return _digest([relative, " ".join(lines[line - 1].split()) if line <= len(lines) else ""])


def file_digest(root: Path, relative: str) -> str:
    """Digest a reviewed file: the whole-module AST for Python, the bytes otherwise."""
    data = (root / relative).read_bytes()
    if relative.endswith(".py"):
        try:
            return _digest([ast.dump(ast.parse(data))])
        except SyntaxError, ValueError:
            pass
    return hashlib.sha256(data).hexdigest()


def issue_source(issue: dict[str, Any]) -> str | None:
    return issue.get("source") or (
        f"{issue['file']}:{issue['line']}" if issue.get("file") else None
    )


def issue_key(issue: dict[str, Any]) -> tuple[str, str, str | None]:
    return (
        str(issue.get("code") or issue.get("topic")),
        str(issue.get("domain")),
        issue_source(issue),
    )


def _file_of(source: str) -> str:
    return source.rsplit(":", 1)[0]


def _line_of(source: str) -> int:
    return int(source.rsplit(":", 1)[1])


def refresh_entries(
    root: Path,
    entries: list[dict[str, Any]],
    issues: list[dict[str, Any]],
    *,
    accept_reviewed: bool,
) -> list[str]:
    """Re-line and re-hash sourced entries in place; return those needing a human re-read.

    Entries and current issues are grouped by (code, domain, file). Within a group an entry
    takes the current issue whose reviewed-definition digest equals its own (the earliest
    unclaimed one), so moved lines follow their definition. Entries left over have a changed
    or removed definition; with ``accept_reviewed`` they take the leftover issues in line
    order when the counts agree, otherwise they stay listed.
    """
    groups: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for issue in issues:
        code, domain, source = issue_key(issue)
        if source is not None:
            groups[(code, domain, _file_of(source))].append(source)
    needs_review: list[str] = []
    by_group: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        if entry["source"]:
            key = (str(entry["issue_code"]), str(entry["domain"]), _file_of(entry["source"]))
            by_group[key].append(entry)
    for key, group_entries in by_group.items():
        candidates = sorted(groups.get(key, []), key=_line_of)
        digests = {source: span_digest(root, source) for source in candidates}
        unmatched: list[dict[str, Any]] = []
        for entry in sorted(group_entries, key=lambda item: _line_of(item["source"])):
            match = next(
                (s for s in candidates if digests[s] == entry["span_sha256"]),
                None,
            )
            if match is None:
                unmatched.append(entry)
                continue
            candidates.remove(match)
            entry["source"] = match
        if unmatched and accept_reviewed and len(unmatched) == len(candidates):
            for entry, source in zip(unmatched, candidates, strict=True):
                entry["source"] = source
                entry["span_sha256"] = digests[source]
            unmatched = []
        needs_review += [
            f"{entry['issue_code']}@{entry['source']}: the reviewed definition changed or is gone"
            for entry in unmatched
        ]
    return needs_review


def refresh_dependencies(
    root: Path, dependencies: list[dict[str, Any]], *, accept_reviewed: bool
) -> list[str]:
    """Re-hash dependency pins whose file is missing or changed only when accepted."""
    needs_review: list[str] = []
    for dependency in dependencies:
        path = str(dependency["path"])
        if not (root / path).is_file():
            needs_review.append(f"{path}: the reviewed file is gone")
            continue
        current = file_digest(root, path)
        if dependency["normalized_sha256"] == current:
            continue
        if accept_reviewed:
            dependency["normalized_sha256"] = current
        else:
            needs_review.append(f"{path}: the reviewed file's content changed")
    return needs_review


def _dump(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2) + "\n"


def refresh(root: Path = ROOT, *, accept_reviewed: bool = False) -> tuple[list[str], list[Path]]:
    """Refresh every record; write them only when nothing needs a human re-read."""
    from tools.release_acceptance import discovery

    review_path = root / DISCOVERY_REVIEW
    review = json.loads(review_path.read_text(encoding="utf-8"))
    issues = discovery.build_report(root)["issues"]
    pending = refresh_entries(root, review["entries"], issues, accept_reviewed=accept_reviewed)
    documents = {review_path: review}
    for relative in DEPENDENCY_RECORDS:
        path = root / relative
        record = json.loads(path.read_text(encoding="utf-8"))
        pending += [
            f"{path.name}: {item}"
            for item in refresh_dependencies(
                root, record["dependencies"], accept_reviewed=accept_reviewed
            )
        ]
        documents[path] = record
    written: list[Path] = []
    if not pending:
        for path, document in documents.items():
            text = _dump(document)
            if path.read_text(encoding="utf-8") != text:
                path.write_text(text, encoding="utf-8")
                written.append(path)
    return sorted(pending), written


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if any(arg != "--accept-reviewed" for arg in args):
        print("usage: review_records [--accept-reviewed]", file=sys.stderr)
        return 2
    pending, written = refresh(accept_reviewed="--accept-reviewed" in args)
    for item in pending:
        print(f"  needs human review: {item}")
    if pending:
        print(f"nothing written; {ACCEPT_HINT}")
        return 1
    for path in written:
        print(f"updated {path.relative_to(ROOT)}")
    print("review records are current" if not written else f"{len(written)} record(s) refreshed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
