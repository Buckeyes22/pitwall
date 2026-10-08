"""Compose the release-acceptance domain inventories into one canonical report.

The individual inventory modules intentionally have narrow scopes.  This facade
joins their rows without changing their semantic identifiers, retains each
module's unresolved issues, and adds only the two surface fields required by
``tools.release_acceptance.evidence``: ``surface_kind`` and ``declared_source``.
A duplicate identifier is retained and reported; it is never made unique by
adding a suffix and it is never silently discarded.

Composition itself touches no application, provider, credential, or external
service. Some existing default discoverers use bounded local parser subprocesses
(for example the Node inventory); that behavior belongs to those entry points
and is preserved rather than hidden. A domain failure is an unresolved issue
while other domains continue, so the report cannot accidentally look complete
because one extractor was unavailable.

Report schema ``release-acceptance-discovery.v1``::

    {
      "schema_version": "release-acceptance-discovery.v1",
      "status": "complete|unresolved",
      "domains": [{"name": ..., "schema_version": ..., "status": ...}],
      "surfaces": [...],
      "issues": [...],
      "deferred_scope": [...]
    }

The complete report is the discovery input consumed by ``evidence.validate``
after its rows are bound to an acceptance matrix; passing only ``surfaces``
would discard the unresolved diagnostics. The
report's ``status`` is ``unresolved`` whenever any domain or composition issue
exists; it is not a release verdict.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "release-acceptance-discovery.v1"

# Keep this order stable.  It is the order in which the report explains its
# inputs; surface rows and issues have their own independent stable sort.
DOMAIN_ORDER = (
    "rest_mcp",
    "cli_dispatch",
    "cli_arguments",
    "config",
    "gateway",
    "ops",
    "routing",
    "routing_contracts",
    "tui",
)
_SOURCE_REF = re.compile(r"^(?P<path>[^:]+):(?P<line>\d+)(?:-\d+)?$")
_EVIDENCE_SOURCE_REF = re.compile(r"^[A-Za-z0-9_./-]+:\d+(?:-\d+)?$")
_HEX_DIGEST = re.compile(r"^(?:sha256:)?(?P<hex>[0-9a-fA-F]{64})$")
# ``source_sha256`` is deliberately domain-specific in the existing units:
# operations hashes the complete source file, while config hashes a declaration
# segment and therefore cannot be compared to the complete file here.  Keep the
# check explicit until each unit publishes a machine-readable digest scope.
_FULL_FILE_DIGEST_DOMAINS = frozenset({"ops"})
# The current evidence validator checks that surface_kind is a non-empty string
# and does not export a SURFACE_KINDS constant. Keep the facade's accepted set
# explicit for the current domain vocabulary until the shared schema defines one.
_KNOWN_SURFACE_KINDS = frozenset(
    {
        "rest",
        "mcp",
        "cli",
        "tui",
        "gateway",
        "plugin",
        "shim",
        "config",
        "install",
        "ops",
        "provider",
        "service",
    }
)

Discoverer = Callable[[Path], Mapping[str, Any] | Sequence[Mapping[str, Any]]]


class DiscoveryCompositionError(ValueError):
    """Raised only for invalid facade arguments, never for one domain failure."""


def _default_discoverers() -> dict[str, Discoverer]:
    """Load the existing domain entry points lazily and without new extractors."""
    from . import (
        cli_arguments,
        cli_inventory,
        config_inventory,
        gateway_inventory,
        inventory,
        ops_inventory,
        routing_contracts,
        routing_inventory,
        tui_inventory,
    )

    return {
        "rest_mcp": inventory.build_report,
        "cli_dispatch": cli_inventory.discover_with_issues,
        "cli_arguments": cli_arguments.discover_with_issues,
        "config": config_inventory.discover_with_issues,
        "gateway": gateway_inventory.discover_with_issues,
        "ops": ops_inventory.discover_with_issues,
        "routing": routing_inventory.discover_with_issues,
        "routing_contracts": routing_contracts.discover_with_issues,
        "tui": tui_inventory.discover_with_issues,
    }


def _canonical_json(value: Any) -> str:
    """Return a deterministic comparison representation for JSON-like values."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=repr)


def _digest(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = _HEX_DIGEST.fullmatch(value.strip())
    return match.group("hex").lower() if match else None


def _source_file(root: Path, source: Any) -> Path | None:
    """Resolve an in-repository ``path:line`` source without guessing external paths."""
    if not isinstance(source, str):
        return None
    match = _SOURCE_REF.fullmatch(source.strip())
    if match is None:
        return None
    relative = Path(match.group("path"))
    candidate = relative.resolve() if relative.is_absolute() else (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _surface_kind_is_valid(value: Any) -> bool:
    return isinstance(value, str) and value.strip() in _KNOWN_SURFACE_KINDS


def _issue(
    code: str,
    message: str,
    *,
    domain: str,
    surface_ids: Sequence[str] = (),
    severity: str = "unresolved",
    status: str = "unresolved",
    **extra: Any,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "code": code,
        "domain": domain,
        "message": message,
        "severity": severity,
        "status": status,
        "surface_ids": list(surface_ids),
    }
    result.update(extra)
    return result


def _domain_report(domain: str, raw: Any) -> tuple[str, list[Any], list[dict[str, Any]]]:
    """Coerce one existing module's report while retaining malformed data as an issue."""
    if isinstance(raw, Mapping):
        schema_version = str(raw.get("schema_version") or "unknown")
        surfaces_value = raw.get("surfaces", [])
        issues_value = raw.get("issues", [])
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        schema_version = "legacy-list"
        surfaces_value = raw
        issues_value = []
    else:
        return (
            "unknown",
            [],
            [
                _issue(
                    "domain-invalid-report",
                    f"{domain} returned {type(raw).__name__}; expected a report mapping or surface list",
                    domain=domain,
                )
            ],
        )

    problems: list[dict[str, Any]] = []
    surfaces = list(surfaces_value) if isinstance(surfaces_value, list) else []
    if not isinstance(surfaces_value, list):
        problems.append(
            _issue(
                "domain-invalid-surfaces",
                f"{domain} report surfaces must be a list",
                domain=domain,
            )
        )
    if isinstance(issues_value, list):
        for original in issues_value:
            if isinstance(original, Mapping):
                issue = copy.deepcopy(dict(original))
                issue.setdefault(
                    "code",
                    str(issue.get("topic") or "deferred-scope")
                    if issue.get("status") == "deferred"
                    else str(issue.get("topic") or "domain-issue"),
                )
                issue.setdefault("domain", domain)
                issue.setdefault("discovery_domain", domain)
                issue.setdefault("surface_ids", [])
                problems.append(issue)
            else:
                problems.append(
                    _issue(
                        "domain-invalid-issue",
                        f"{domain} emitted a non-mapping issue ({type(original).__name__})",
                        domain=domain,
                    )
                )
    elif issues_value not in (None, []):
        problems.append(
            _issue(
                "domain-invalid-issues",
                f"{domain} report issues must be a list",
                domain=domain,
            )
        )
    return schema_version, surfaces, problems


def _source_review(root: Path) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Link source review as provenance without treating it as approval."""
    relative = "release_acceptance/discovery-review.json"
    path = root / relative
    base = {"path": relative, "auto_approved": False}
    if not path.is_file():
        return {**base, "status": "absent"}, None
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except FileNotFoundError, OSError, ValueError:
        return (
            {**base, "status": "outside_root"},
            _issue(
                "source-review-outside-root",
                f"{relative} resolves outside the candidate root and was not read",
                domain="composition",
            ),
        )
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return (
            {**base, "status": "invalid"},
            _issue(
                "source-review-invalid",
                f"{relative} could not be read as JSON: {type(exc).__name__}: {exc}",
                domain="composition",
            ),
        )
    if not isinstance(payload, Mapping) or not isinstance(payload.get("entries"), list):
        return (
            {**base, "status": "invalid"},
            _issue(
                "source-review-invalid",
                f"{relative} must contain an entries list",
                domain="composition",
            ),
        )
    return (
        {
            **base,
            "status": "linked_not_approved",
            "schema_version": str(payload.get("schema_version") or "unknown"),
            "entry_count": len(payload["entries"]),
        },
        None,
    )


def _canonical_surface(
    root: Path,
    domain: str,
    schema_version: str,
    original: Any,
) -> tuple[Any, list[dict[str, Any]]]:
    """Add evidence-compatible fields while preserving the original row verbatim."""
    if not isinstance(original, Mapping):
        return original, [
            _issue(
                "invalid-surface-row",
                f"{domain} emitted a {type(original).__name__} surface row",
                domain=domain,
            )
        ]

    row = copy.deepcopy(dict(original))
    surface_id = row.get("surface_id")
    source = row.get("source")
    surface_kind = row["surface_kind"] if "surface_kind" in row else row.get("kind")
    declared_source = row.get("declared_source")
    if declared_source is None and isinstance(source, str):
        declared_source = [source]
    elif isinstance(declared_source, str):
        declared_source = [declared_source]
    if "surface_kind" not in row and surface_kind is not None:
        row["surface_kind"] = surface_kind
    if "declared_source" not in row and declared_source is not None:
        row["declared_source"] = declared_source

    # Origin is outside metadata so no domain-specific contract metadata is
    # rewritten.  The original semantic ID remains the canonical ID.
    row["origin"] = {
        "domain": domain,
        "schema_version": schema_version,
        "surface_id": surface_id,
    }
    issues: list[dict[str, Any]] = []
    if not isinstance(surface_id, str) or not surface_id.strip():
        issues.append(
            _issue(
                "invalid-surface-id",
                f"{domain} emitted a surface without a non-empty surface_id",
                domain=domain,
            )
        )
    if not _surface_kind_is_valid(surface_kind):
        issues.append(
            _issue(
                "invalid-surface-kind",
                f"surface {surface_id!r} from {domain} has unsupported surface_kind {surface_kind!r}",
                domain=domain,
                surface_ids=[surface_id] if isinstance(surface_id, str) else [],
            )
        )
    valid_sources = (
        isinstance(declared_source, list)
        and bool(declared_source)
        and all(
            isinstance(item, str) and _EVIDENCE_SOURCE_REF.fullmatch(item.strip())
            for item in declared_source
        )
    )
    if not valid_sources:
        issues.append(
            _issue(
                "invalid-declared-source",
                f"surface {surface_id!r} from {domain} has invalid declared_source references",
                domain=domain,
                surface_ids=[surface_id] if isinstance(surface_id, str) else [],
            )
        )

    # The existing config/ops rows publish a file-byte source_sha256.  Checking
    # it here catches a stale report without attempting to recreate any
    # domain-specific declaration contract.  External/generated source paths
    # are deliberately skipped because they are not candidate files.
    metadata = row.get("metadata")
    if domain in _FULL_FILE_DIGEST_DOMAINS:
        expected_value = metadata.get("source_sha256") if isinstance(metadata, Mapping) else None
        expected = _digest(expected_value)
        source_path = _source_file(root, source)
        if expected is None:
            issues.append(
                _issue(
                    "invalid-source-contract",
                    f"surface {surface_id!r} from {domain} has no valid full-file source_sha256",
                    domain=domain,
                    surface_ids=[surface_id] if isinstance(surface_id, str) else [],
                    source=source,
                )
            )
        elif source_path is None:
            issues.append(
                _issue(
                    "unavailable-source-contract",
                    f"full-file source contract for {source!r} is missing or outside the candidate root",
                    domain=domain,
                    surface_ids=[surface_id] if isinstance(surface_id, str) else [],
                    source=source,
                    expected_sha256=expected,
                )
            )
        else:
            try:
                actual = _file_sha256(source_path)
            except OSError as exc:
                issues.append(
                    _issue(
                        "unavailable-source-contract",
                        f"full-file source contract for {source!r} could not be read",
                        domain=domain,
                        surface_ids=[surface_id] if isinstance(surface_id, str) else [],
                        source=source,
                        expected_sha256=expected,
                        error_type=type(exc).__name__,
                    )
                )
                return row, issues
            if actual != expected:
                issues.append(
                    _issue(
                        "stale-source-contract",
                        f"source bytes for {source!r} no longer match metadata.source_sha256",
                        domain=domain,
                        surface_ids=[surface_id] if isinstance(surface_id, str) else [],
                        source=source,
                        expected_sha256=expected,
                        actual_sha256=actual,
                    )
                )
    return row, issues


def _surface_sort_key(row: Any) -> tuple[str, str, str, str]:
    if not isinstance(row, Mapping):
        return ("", "", "", _canonical_json(row))
    origin = row.get("origin")
    domain = origin.get("domain", "") if isinstance(origin, Mapping) else ""
    return (
        str(row.get("surface_id", "")),
        str(domain),
        str(row.get("source", "")),
        _canonical_json(row),
    )


def _issue_sort_key(issue: Mapping[str, Any]) -> tuple[str, str, str, str, str]:
    surface_ids = issue.get("surface_ids")
    if not isinstance(surface_ids, list):
        surface_ids = []
    return (
        str(issue.get("code", "")),
        str(issue.get("discovery_domain", issue.get("domain", ""))),
        ",".join(str(value) for value in surface_ids),
        str(issue.get("source", "")),
        _canonical_json(issue),
    )


def discover_with_issues(
    root: str | Path = ROOT,
    *,
    discoverers: Mapping[str, Discoverer] | None = None,
) -> dict[str, Any]:
    """Compose every existing inventory and retain unresolved scope explicitly.

    ``discoverers`` is a test seam and may override any named default domain.  A
    missing custom name is rejected; a discoverer exception is captured as an
    unresolved domain issue and does not prevent other domains from running.
    """
    candidate_root = Path(root).resolve()
    selected = _default_discoverers()
    if discoverers is not None:
        unknown = sorted(set(discoverers) - set(DOMAIN_ORDER))
        if unknown:
            raise DiscoveryCompositionError(f"unknown discovery domain(s): {', '.join(unknown)}")
        selected.update(discoverers)

    surfaces: list[Any] = []
    issues: list[dict[str, Any]] = []
    domains: list[dict[str, Any]] = []
    domain_failed: set[str] = set()

    source_review, source_review_issue = _source_review(candidate_root)
    if source_review_issue is not None:
        issues.append(source_review_issue)

    for domain in DOMAIN_ORDER:
        discoverer = selected[domain]
        domain_issues: list[dict[str, Any]] = []
        try:
            raw = discoverer(candidate_root)
            schema_version, original_surfaces, report_issues = _domain_report(domain, raw)
            domain_issues.extend(report_issues)
        except Exception as exc:  # reason: one static inventory must not hide other domains
            schema_version = "unknown"
            original_surfaces = []
            domain_failed.add(domain)
            domain_issues.append(
                _issue(
                    "domain-failed",
                    f"{domain} discovery failed with {type(exc).__name__}: {exc}",
                    domain=domain,
                )
            )

        domain_rows: list[Any] = []
        for original in original_surfaces:
            row, row_issues = _canonical_surface(candidate_root, domain, schema_version, original)
            domain_rows.append(row)
            domain_issues.extend(row_issues)
        surfaces.extend(domain_rows)
        issues.extend(domain_issues)
        domains.append(
            {
                "name": domain,
                "schema_version": schema_version,
                "surface_count": len(domain_rows),
                "issue_count": len(domain_issues),
                "status": "unresolved" if domain_issues else "complete",
            }
        )

    by_id: dict[str, list[tuple[str, str]]] = {}
    for row in surfaces:
        if not isinstance(row, Mapping):
            continue
        surface_id = row.get("surface_id")
        if not isinstance(surface_id, str) or not surface_id.strip():
            continue
        origin = row.get("origin")
        domain = origin.get("domain", "unknown") if isinstance(origin, Mapping) else "unknown"
        by_id.setdefault(surface_id, []).append((str(domain), str(row.get("source", ""))))
    for surface_id, occurrences in sorted(by_id.items()):
        if len(occurrences) < 2:
            continue
        domains_for_id = sorted({domain for domain, _ in occurrences})
        code = "surface-id-collision" if len(domains_for_id) > 1 else "duplicate-surface-id"
        issues.append(
            _issue(
                code,
                f"surface_id {surface_id!r} occurs {len(occurrences)} times; rows were retained",
                domain="composition",
                surface_ids=[surface_id],
                domains=domains_for_id,
                occurrences=[
                    {"domain": domain, "source": source} for domain, source in sorted(occurrences)
                ],
            )
        )

    surfaces.sort(key=_surface_sort_key)
    issues.sort(key=_issue_sort_key)
    deferred_scope = [
        issue
        for issue in issues
        if issue.get("status") in {"deferred", "unresolved", "partial"}
        or issue.get("severity") == "unresolved"
    ]
    unresolved = bool(issues) or bool(domain_failed)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "unresolved" if unresolved else "complete",
        "domains": domains,
        "source_review": source_review,
        "surfaces": surfaces,
        "issues": issues,
        "deferred_scope": deferred_scope,
    }


def discover(
    root: str | Path = ROOT,
    *,
    discoverers: Mapping[str, Discoverer] | None = None,
) -> list[Any]:
    """Return the composed canonical surface rows, retaining unresolved duplicates."""
    report = discover_with_issues(root, discoverers=discoverers)
    surfaces = report.get("surfaces")
    return list(surfaces) if isinstance(surfaces, list) else []


def build_report(
    root: str | Path = ROOT,
    *,
    discoverers: Mapping[str, Discoverer] | None = None,
) -> dict[str, Any]:
    """Alias matching the HTTP/MCP inventory's report-building API."""
    return discover_with_issues(root, discoverers=discoverers)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="release-acceptance-discovery",
        description="Compose all static release-acceptance domain inventories.",
    )
    parser.add_argument("--root", type=Path, default=ROOT, help="Repository root to discover.")
    parser.add_argument("--output", type=Path, help="Write the JSON report to this path.")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    payload = json.dumps(build_report(args.root), indent=2, sort_keys=True) + "\n"
    if args.output is None:
        sys.stdout.write(payload)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
