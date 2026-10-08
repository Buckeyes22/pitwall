"""Assemble a deterministic draft acceptance matrix from existing evidence inputs.

This module is a bounded Phase 2 assembler.  It composes the existing discovery
facade, candidate identity, static test-node index, declaration extract, reviewed
binding extract, and evidence renderer.  It does not execute tests, collect
pytest nodes at runtime, call providers, or make a release claim.

Every discovered row is retained.  Valid source-reviewed bindings become draft
``not_run`` cases only after the source file hash, source reference, and exact
static test node are checked.  Stale, malformed, ambiguous, unknown, skipped,
parameterized, or uncollectable bindings become explicit gaps.  A surface without
a valid binding receives a required unreviewed requirement and an open gap; no
shared or generic case is treated as coverage for another journey.

The caller must provide a new external output directory. It is created privately
with mode 0700 and each artifact is created as an exclusive regular file. The
candidate snapshot is built before output files are written, so generated reports
cannot enter their own candidate identity.
"""

from __future__ import annotations

import argparse
import copy
import fnmatch
import hashlib
import json
import os
import re
import stat
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from . import candidate as candidate_tool
from . import discovery, evidence, test_index

ROOT = Path(__file__).resolve().parents[2]
MATRIX_SCHEMA_VERSION = evidence.SCHEMA_VERSION
JSONL_SCHEMA_VERSION = "acceptance.v1"
GAP_SCHEMA_VERSION = "release-acceptance-gap-ledger.v1"
COLLECTION_SCHEMA_VERSION = "release-acceptance-pytest-collection.v1"
BINDINGS_RELATIVE = Path("release_acceptance/reviewed-bindings.json")
DECLARATIONS_RELATIVE = Path("release_acceptance/declarations.json")
_SOURCE_REF = re.compile(r"^(?P<path>[A-Za-z0-9_./-]+):(?P<line>\d+)(?:-\d+)?$")
_DIGEST = re.compile(r"^(?:sha256:)?(?P<hex>[0-9a-fA-F]{64})$")


class MatrixAssemblyError(ValueError):
    """Raised for invalid output scope or caller-supplied assembler arguments."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=repr)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(_canonical(value).encode("utf-8"))


def _digest(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = _DIGEST.fullmatch(value.strip())
    return match.group("hex").lower() if match else None


def _inside(root: Path, path: Path) -> Path | None:
    """Resolve a path only when it remains inside the candidate root."""
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except FileNotFoundError, OSError, ValueError:
        return None
    return resolved


def _source_file(root: Path, source: Any) -> tuple[Path | None, int | None, str | None]:
    if not isinstance(source, str):
        return None, None, "source must be a path:line reference"
    match = _SOURCE_REF.fullmatch(source.strip())
    if match is None:
        return None, None, "source is not a path:line reference"
    line = int(match.group("line"))
    path = _inside(root, root / match.group("path"))
    if path is None:
        return None, line, "source file is missing or resolves outside the candidate root"
    return path, line, None


def _read_json_input(root: Path, path: Path, label: str) -> tuple[Any, dict[str, Any] | None]:
    safe = _inside(root, path)
    if safe is None:
        return None, {
            "kind": "input-unavailable",
            "source": str(path),
            "reason": f"{label} is missing or outside the candidate root",
        }
    try:
        return json.loads(safe.read_text(encoding="utf-8")), None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, {
            "kind": "input-invalid",
            "source": str(path),
            "reason": f"{label} could not be read: {type(exc).__name__}",
        }


def _read_external_collection_input(root: Path, path: Path) -> tuple[Any, dict[str, Any] | None]:
    """Read an explicit regular receipt path outside ``root``."""
    requested = path.expanduser()
    if not requested.is_absolute():
        requested = Path.cwd() / requested
    if os.path.lexists(requested) and requested.is_symlink():
        return None, {
            "kind": "collection-report-path-escape",
            "source": str(requested),
            "reason": "collection report symlinks are refused",
        }
    try:
        safe = requested.resolve(strict=True)
        safe.relative_to(root)
    except ValueError:
        pass
    except FileNotFoundError, OSError:
        return None, {
            "kind": "collection-report-unavailable",
            "source": str(requested),
            "reason": "collection report is missing or unreadable",
        }
    else:
        return None, {
            "kind": "collection-report-path-escape",
            "source": str(requested),
            "reason": "collection report must resolve outside the candidate root",
        }
    try:
        mode = requested.stat().st_mode
        if not stat.S_ISREG(mode):
            return None, {
                "kind": "collection-report-invalid",
                "source": str(requested),
                "reason": "collection report is not a regular file",
            }
        return json.loads(requested.read_text(encoding="utf-8")), None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, {
            "kind": "collection-report-invalid",
            "source": str(requested),
            "reason": f"collection report could not be read: {type(exc).__name__}",
        }


def _collection_report_validation(
    root: Path, report: Any
) -> tuple[Mapping[str, Any] | None, list[str]]:
    """Validate receipt provenance and every source row before matrix use."""
    errors: list[str] = []
    if not isinstance(report, Mapping):
        return None, ["collection report is not an object"]
    if report.get("schema_version") != COLLECTION_SCHEMA_VERSION:
        errors.append("collection report schema_version is unsupported")
    if report.get("collect_only") is not True:
        errors.append("collection report does not prove --collect-only")
    if report.get("exitstatus") != 0:
        errors.append("collection report exitstatus is not zero")
    if report.get("status") != "complete":
        errors.append("collection report status is not complete")
    for field in ("collection_failures", "issues"):
        value = report.get(field)
        if not isinstance(value, list):
            errors.append(f"collection report {field} is not a list")
        elif value:
            errors.append(f"collection report contains {field}")
    candidate_root = report.get("candidate_root")
    try:
        if not isinstance(candidate_root, str) or Path(candidate_root).resolve(strict=True) != root:
            errors.append("collection report candidate_root does not match the candidate")
    except OSError, RuntimeError:
        errors.append("collection report candidate_root is unavailable")
    items = report.get("items")
    if not isinstance(items, list):
        return None, [*errors, "collection report items is not a list"]
    seen: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            errors.append(f"collection item {index} is not an object")
            continue
        nodeid = item.get("collected_id")
        if not isinstance(nodeid, str) or not nodeid:
            errors.append(f"collection item {index} has no collected_id")
        elif nodeid in seen:
            errors.append(f"collection item {index} duplicates collected_id {nodeid}")
        else:
            seen.add(nodeid)
        source_file = item.get("source_file")
        source_path: Path | None = None
        if (
            not isinstance(source_file, str)
            or not source_file
            or Path(source_file).is_absolute()
            or ".." in Path(source_file).parts
        ):
            errors.append(f"collection item {index} has a path-escaping source_file")
        else:
            source_path = _inside(root, root / source_file)
            if source_path is None:
                errors.append(
                    f"collection item {index} source_file is missing or outside the candidate"
                )
        expected_hash = _digest(item.get("source_sha256"))
        if expected_hash is None:
            errors.append(f"collection item {index} source_sha256 is malformed")
        elif source_path is not None:
            try:
                if _sha256_bytes(source_path.read_bytes()) != expected_hash:
                    errors.append(f"collection item {index} source_sha256 is stale")
            except OSError, UnicodeDecodeError:
                errors.append(f"collection item {index} source_file is unreadable")
        if not isinstance(item.get("line"), int) or item.get("line", 0) < 1:
            errors.append(f"collection item {index} line is invalid")
        if not isinstance(item.get("function"), str) or not item.get("function", "").strip():
            errors.append(f"collection item {index} function is missing")
        if item.get("framework") != "pytest":
            errors.append(f"collection item {index} framework is not pytest")
    return report, errors


def _input_path(root: Path, value: str | Path | None, default: Path) -> Path:
    """Resolve caller-provided relative inputs from the candidate root."""
    path = default if value is None else Path(value).expanduser()
    return path if path.is_absolute() else root / path


def _candidate_projection(record: Mapping[str, Any]) -> dict[str, Any]:
    raw_git = record.get("git")
    git = raw_git if isinstance(raw_git, Mapping) else {}
    raw_tools = record.get("tools")
    tools = raw_tools if isinstance(raw_tools, Mapping) else {}
    manifest_raw = record.get("manifest")
    manifest: list[Any] = manifest_raw if isinstance(manifest_raw, list) else []
    dependencies_raw = record.get("dependencies")
    dependencies: list[Any] = dependencies_raw if isinstance(dependencies_raw, list) else []
    artifacts_raw = record.get("artifacts")
    artifacts: list[Any] = artifacts_raw if isinstance(artifacts_raw, list) else []
    raw_git_status = record.get("git_status")
    clean = (
        raw_git_status.get("clean") if isinstance(raw_git_status, Mapping) else record.get("clean")
    )
    dependency_locks = {
        str(item.get("path")): item.get("sha256")
        for item in dependencies
        if isinstance(item, Mapping) and isinstance(item.get("path"), str)
    }
    artifact_digests = {
        str(item.get("name")): item.get("sha256")
        for item in artifacts
        if isinstance(item, Mapping) and isinstance(item.get("name"), str)
    }
    return {
        "candidate_id": record.get("candidate_id"),
        "candidate_schema_version": record.get("schema_version"),
        "broker_commit": git.get("commit", record.get("broker_commit")),
        "component_commits": {},
        "dirty_manifest_sha256": _sha256_json(manifest),
        "dependency_locks": dict(sorted(dependency_locks.items())),
        "artifact_digests": dict(sorted(artifact_digests.items())),
        "platform": {
            key: tools.get(key)
            for key in ("python", "node", "uv", "platform", "machine")
            if key in tools
        },
        "clean": clean,
        "release_ready": record.get("release_ready"),
        "candidate_issues": sorted(str(issue) for issue in record.get("issues", []))
        if isinstance(record.get("issues"), list)
        else [],
    }


def _surface_key(row: Any) -> tuple[str, str, str, str]:
    if not isinstance(row, Mapping):
        return ("", "", "", _canonical(row))
    origin = row.get("origin")
    domain = origin.get("domain", "") if isinstance(origin, Mapping) else ""
    return (
        str(row.get("surface_id", "")),
        str(domain),
        str(row.get("source", "")),
        _canonical(row),
    )


def _surface_hints(surface_id: str, declarations: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []
    for declaration in declarations:
        declaration_id = declaration.get("declaration_id")
        source = declaration.get("source")
        scope = declaration.get("scope")
        patterns = scope.get("patterns") if isinstance(scope, Mapping) else None
        if not isinstance(declaration_id, str) or not isinstance(patterns, list):
            continue
        for selector in patterns:
            if not isinstance(selector, str) or not fnmatch.fnmatchcase(surface_id, selector):
                continue
            hints.append(
                {
                    "declaration_id": declaration_id,
                    "selector": selector,
                    "source": source,
                    "status": declaration.get("status"),
                    "review_status": "hint_only",
                    "auto_approved": False,
                    "implies_tested_coverage": False,
                }
            )
    return sorted(hints, key=lambda item: _canonical(item))


def _gap(
    kind: str, reason: str, *, candidate_id: Any, surface_id: Any = None, **details: Any
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "candidate_id": candidate_id,
        "kind": kind,
        "status": "open",
        "surface_id": surface_id,
        "reason": reason,
    }
    result.update(details)
    return result


def _finalize_gaps(raw_gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(raw_gaps, key=_canonical)
    counts: Counter[str] = Counter()
    finalized: list[dict[str, Any]] = []
    for gap in ordered:
        base = "gap:" + _sha256_json(gap)[:20]
        counts[base] += 1
        gap_id = base if counts[base] == 1 else f"{base}:{counts[base]:02d}"
        finalized.append({"gap_id": gap_id, **gap})
    return finalized


def _case_id(prefix: str, value: Any, ordinal: int = 0) -> str:
    suffix = _sha256_json(value)[:20]
    return f"{prefix}-{suffix}" if ordinal == 0 else f"{prefix}-{suffix}-{ordinal:02d}"


def _draft_case(
    *,
    case_id: str,
    surface_id: str,
    applicability: str,
    scenario_id: str,
    required_lanes: list[str],
    test_node_ids: list[str],
    expected: str,
    draft_kind: str,
    candidate_id: Any,
    source_review: Mapping[str, Any] | None = None,
    reviewer: Any = None,
    limits: Any = None,
    framework: Any = None,
) -> dict[str, Any]:
    lane_results = dict.fromkeys(required_lanes, "not_run")
    return {
        "case_id": case_id,
        "surface_id": surface_id,
        "scenario_id": scenario_id,
        "journey_id": None,
        "applicability": applicability,
        "required_lanes": required_lanes,
        "required_proof_lanes": list(required_lanes),
        "test_node_ids": test_node_ids,
        "lane_test_node_ids": None,
        "manual_steps": [],
        "expected_vs_actual": {"expected": expected, "actual": "not run"},
        "oracle": {"expected": expected, "actual": "not run", "observed_success": False},
        "status": "not_run",
        "lane_results": lane_results,
        "evidence": [],
        "exception": None,
        "reviewer": reviewer,
        "timestamps": {"created": None, "started": None, "finished": None},
        "run": {
            "run_id": None,
            "command": None,
            "exit_code": None,
            "artifacts": [],
            "evidence_sha256": None,
        },
        "provenance": {"harness": framework, "provider": None, "model": None, "dependencies": None},
        "draft": {
            "kind": draft_kind,
            "candidate_id": candidate_id,
            "source_review": dict(source_review) if isinstance(source_review, Mapping) else None,
            "limits": limits,
            "runtime_collection_verified": False,
        },
    }


def _binding_sort_key(item: tuple[int, Mapping[str, Any]]) -> tuple[str, int]:
    index, binding = item
    return (_canonical(binding), index)


def _test_index_maps(
    report: Any,
) -> tuple[dict[str, list[Mapping[str, Any]]], list[Mapping[str, Any]]]:
    if not isinstance(report, Mapping):
        return {}, []
    node_map: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in report.get("nodes", []) if isinstance(report.get("nodes"), list) else []:
        if isinstance(row, Mapping) and isinstance(row.get("node_id"), str):
            node_map[row["node_id"]].append(row)
    templates = (
        [row for row in report.get("templates", []) if isinstance(row, Mapping)]
        if isinstance(report.get("templates"), list)
        else []
    )
    return node_map, templates


def _node_function(node_id: Any) -> str:
    if not isinstance(node_id, str):
        return ""
    suffix = node_id.split("::", 1)[1] if "::" in node_id else node_id
    return "::".join(part.split("[", 1)[0] for part in suffix.split("::"))


def _template_function(template: Mapping[str, Any]) -> str | None:
    metadata = template.get("metadata")
    if isinstance(metadata, Mapping) and isinstance(metadata.get("function"), str):
        return str(metadata["function"])
    template_id = template.get("template_id")
    if not isinstance(template_id, str) or "::" not in template_id:
        return None
    suffix = template_id.split("::", 1)[1]
    return suffix.rsplit(":", 1)[0]


def _matching_templates(
    templates: list[Mapping[str, Any]], source_path: str, node_id: Any
) -> list[Mapping[str, Any]]:
    function = _node_function(node_id)
    matches: list[Mapping[str, Any]] = []
    for template in templates:
        if template.get("path") != source_path:
            continue
        expected_function = _template_function(template)
        if expected_function is None or expected_function == function:
            matches.append(template)
    return matches


def _collection_matches(
    report: Mapping[str, Any], binding: Mapping[str, Any]
) -> list[Mapping[str, Any]]:
    items = report.get("items")
    if not isinstance(items, list):
        return []
    requested = binding.get("collected_test_node_id") or binding.get("test_node_id")
    if not isinstance(requested, str) or not requested:
        return []
    exact = [
        item
        for item in items
        if isinstance(item, Mapping) and item.get("collected_id") == requested
    ]
    if exact:
        return exact
    # A base static node is never enough when it expands to more than one
    # parameter.  This branch is useful only when the receipt proves exactly
    # one concrete collected ID for that base.
    return [
        item
        for item in items
        if isinstance(item, Mapping)
        and isinstance(item.get("collected_id"), str)
        and item["collected_id"].split("[", 1)[0] == requested
    ]


def _binding_case(
    root: Path,
    binding: Mapping[str, Any],
    binding_index: int,
    candidate_id: Any,
    surface_rows: list[Mapping[str, Any]],
    node_map: dict[str, list[Mapping[str, Any]]],
    templates: list[Mapping[str, Any]],
    collection_report: Mapping[str, Any] | None,
    collection_errors: list[str],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    surface_id = binding.get("surface_id")
    source = binding.get("source")
    gaps: list[dict[str, Any]] = []
    if not isinstance(surface_id, str) or not surface_id.strip():
        return None, [
            _gap(
                "invalid-binding",
                "binding has no non-empty surface_id",
                candidate_id=candidate_id,
                binding_index=binding_index,
            )
        ]
    matching_surfaces = [row for row in surface_rows if row.get("surface_id") == surface_id]
    if not matching_surfaces:
        return None, [
            _gap(
                "unknown-binding-surface",
                "binding surface_id is absent from discovery",
                candidate_id=candidate_id,
                surface_id=surface_id,
                binding_index=binding_index,
            )
        ]
    if len(matching_surfaces) != 1:
        return None, [
            _gap(
                "ambiguous-binding-surface",
                "binding surface_id occurs more than once in discovery",
                candidate_id=candidate_id,
                surface_id=surface_id,
                binding_index=binding_index,
                occurrences=len(matching_surfaces),
            )
        ]
    if binding.get("status") != "not_run":
        return None, [
            _gap(
                "binding-status-not-run-required",
                "reviewed binding is not draft not_run; no result is imported",
                candidate_id=candidate_id,
                surface_id=surface_id,
                binding_index=binding_index,
                binding_status=binding.get("status"),
            )
        ]
    missing_review_fields: list[str] = []
    for field in ("reviewed_oracle", "reviewer", "review_state"):
        value = binding.get(field)
        if not isinstance(value, str) or not value.strip():
            missing_review_fields.append(field)
    if missing_review_fields:
        return None, [
            _gap(
                "incomplete-binding-review",
                "source-reviewed binding is missing required review metadata",
                candidate_id=candidate_id,
                surface_id=surface_id,
                binding_index=binding_index,
                missing_fields=missing_review_fields,
            )
        ]
    lane = binding.get("proof_lane")
    if not isinstance(lane, str) or lane not in evidence.SUPPORTED_LANES:
        return None, [
            _gap(
                "invalid-binding-lane",
                "binding proof_lane is not an evidence lane",
                candidate_id=candidate_id,
                surface_id=surface_id,
                binding_index=binding_index,
                proof_lane=lane,
            )
        ]

    source_path, source_line, source_error = _source_file(root, source)
    if source_error is not None or source_path is None or source_line is None:
        return None, [
            _gap(
                "invalid-binding-source",
                source_error or "binding source is unavailable",
                candidate_id=candidate_id,
                surface_id=surface_id,
                binding_index=binding_index,
                source=source,
            )
        ]
    try:
        source_bytes = source_path.read_bytes()
        source_text = source_bytes.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return None, [
            _gap(
                "unreadable-binding-source",
                f"binding source could not be read: {type(exc).__name__}",
                candidate_id=candidate_id,
                surface_id=surface_id,
                binding_index=binding_index,
                source=source,
            )
        ]
    if source_line > len(source_text.splitlines()) or source_line < 1:
        return None, [
            _gap(
                "binding-source-line-missing",
                "binding source line is outside the current file",
                candidate_id=candidate_id,
                surface_id=surface_id,
                binding_index=binding_index,
                source=source,
            )
        ]
    expected_hash = _digest(binding.get("test_source_sha256"))
    actual_hash = _sha256_bytes(source_bytes)
    if expected_hash is None:
        return None, [
            _gap(
                "malformed-binding-hash",
                "binding test_source_sha256 is not a sha256 digest",
                candidate_id=candidate_id,
                surface_id=surface_id,
                binding_index=binding_index,
                source=source,
            )
        ]
    if expected_hash != actual_hash:
        return None, [
            _gap(
                "stale-binding",
                "binding test source hash does not match the current file",
                candidate_id=candidate_id,
                surface_id=surface_id,
                binding_index=binding_index,
                source=source,
                expected_sha256=expected_hash,
                actual_sha256=actual_hash,
            )
        ]

    node_id = binding.get("test_node_id")
    source_relative = source_path.relative_to(root).as_posix()
    template_matches = _matching_templates(templates, source_relative, node_id)
    parameterized = bool(template_matches) or (isinstance(node_id, str) and "[" in node_id)
    selected_node_id = node_id
    runtime_collection_verified = False
    parameterized_template_used = False
    if parameterized:
        if collection_errors:
            return None, [
                _gap(
                    "invalid-collection-report",
                    "explicit pytest collection receipt is stale, malformed, or unresolved",
                    candidate_id=candidate_id,
                    surface_id=surface_id,
                    binding_index=binding_index,
                    test_node_id=node_id,
                    collection_errors=list(collection_errors),
                )
            ]
        if collection_report is None:
            return None, [
                _gap(
                    "parameterized-node-needs-collection-proof",
                    "parameterized or dynamic test requires an explicit pytest collection receipt",
                    candidate_id=candidate_id,
                    surface_id=surface_id,
                    binding_index=binding_index,
                    test_node_id=node_id,
                    templates=[template.get("template_id") for template in template_matches],
                )
            ]
        collected = _collection_matches(collection_report, binding)
        if len(collected) != 1:
            return None, [
                _gap(
                    "collection-node-not-unique",
                    "collection receipt does not identify exactly one concrete parameterized node",
                    candidate_id=candidate_id,
                    surface_id=surface_id,
                    binding_index=binding_index,
                    test_node_id=node_id,
                    collected_count=len(collected),
                )
            ]
        item = collected[0]
        if item.get("source_file") != source_relative or item.get("source_sha256") != actual_hash:
            return None, [
                _gap(
                    "collection-source-mismatch",
                    "collected node source does not match the reviewed binding source",
                    candidate_id=candidate_id,
                    surface_id=surface_id,
                    binding_index=binding_index,
                    test_node_id=node_id,
                    source=source,
                )
            ]
        item_function = item.get("function")
        if template_matches and not any(
            _template_function(template) in (None, item_function) for template in template_matches
        ):
            return None, [
                _gap(
                    "collection-template-mismatch",
                    "collected node function does not match the static parameterized template",
                    candidate_id=candidate_id,
                    surface_id=surface_id,
                    binding_index=binding_index,
                    test_node_id=node_id,
                )
            ]
        selected_node_id = item.get("collected_id")
        runtime_collection_verified = True
        parameterized_template_used = True
        static_node_id = node_id.split("[", 1)[0] if isinstance(node_id, str) else node_id
        nodes = node_map.get(static_node_id, []) if isinstance(static_node_id, str) else []
    else:
        nodes = node_map.get(node_id, []) if isinstance(node_id, str) else []
    node: Mapping[str, Any]
    if len(nodes) == 0 and parameterized and len(template_matches) == 1:
        # The static index intentionally emits a template instead of a fake
        # concrete node for pytest parameterization.  The validated receipt
        # supplies the concrete identity; retain the template's framework and
        # marker flags for the remaining static checks.
        template = template_matches[0]
        node = {
            "node_id": str(selected_node_id),
            "framework": template.get("framework"),
            "skip_indicated": template.get("skip_indicated", False),
            "expected_failure": template.get("expected_failure", False),
        }
    elif len(nodes) == 1:
        node = nodes[0]
    else:
        kind = "missing-test-node"
        return None, [
            _gap(
                kind,
                "exact static test node is not uniquely present; runtime collection is unverified",
                candidate_id=candidate_id,
                surface_id=surface_id,
                binding_index=binding_index,
                test_node_id=node_id,
                templates=[template.get("template_id") for template in template_matches],
            )
        ]
    node_path = str(node.get("node_id", "")).split("::", 1)[0]
    if node_path != source_path.relative_to(root).as_posix():
        return None, [
            _gap(
                "binding-node-source-mismatch",
                "test node path does not match binding source file",
                candidate_id=candidate_id,
                surface_id=surface_id,
                binding_index=binding_index,
                test_node_id=node_id,
                source=source,
            )
        ]
    if binding.get("framework") is not None and binding.get("framework") != node.get("framework"):
        return None, [
            _gap(
                "binding-framework-mismatch",
                "binding framework does not match the static node",
                candidate_id=candidate_id,
                surface_id=surface_id,
                binding_index=binding_index,
                framework=binding.get("framework"),
                node_framework=node.get("framework"),
            )
        ]
    if node.get("skip_indicated") or node.get("expected_failure"):
        return None, [
            _gap(
                "non-runnable-binding-node",
                "binding node carries skip or expected-failure indication",
                candidate_id=candidate_id,
                surface_id=surface_id,
                binding_index=binding_index,
                test_node_id=node_id,
            )
        ]

    case_id = _case_id("CASE-BIND", {"binding": dict(binding), "index": binding_index})
    case = _draft_case(
        case_id=case_id,
        surface_id=surface_id,
        applicability="required",
        scenario_id=f"draft:source-reviewed:{lane}",
        required_lanes=[lane],
        test_node_ids=[str(selected_node_id)],
        expected=binding["reviewed_oracle"],
        draft_kind="source_reviewed_binding",
        candidate_id=candidate_id,
        source_review={"review_status": "source_reviewed_not_execution_approved", "source": source},
        reviewer=binding.get("reviewer"),
        limits=binding.get("limits"),
        framework=binding.get("framework"),
    )
    case["draft"]["binding_index"] = binding_index
    case["draft"]["test_source_validation"] = {
        "source_file_present": True,
        "source_sha256_matches": True,
        "static_node_present": True,
        "runtime_collection_verified": runtime_collection_verified,
        "parameterized_template_used": parameterized_template_used,
    }
    case["draft"]["runtime_collection_verified"] = runtime_collection_verified
    if runtime_collection_verified:
        case["draft"]["collected_test_node_id"] = str(selected_node_id)
    case["reproduction"] = {
        "command": f"uv run pytest -q {selected_node_id}"
        if binding.get("framework") == "pytest"
        else None,
        "preconditions": ["candidate-matched source and static node"],
        "checksums": [expected_hash],
    }
    return case, gaps


def _assemble(
    root: Path,
    *,
    candidate_record: Mapping[str, Any],
    discovery_report: Mapping[str, Any],
    declarations: Any,
    bindings: Any,
    test_index_report: Mapping[str, Any],
    collection_report: Mapping[str, Any] | None = None,
    collection_errors: list[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    candidate = _candidate_projection(candidate_record)
    candidate_id = candidate.get("candidate_id")
    raw_surfaces = discovery_report.get("surfaces")
    surfaces = copy.deepcopy(raw_surfaces) if isinstance(raw_surfaces, list) else []
    discovery_issues = (
        copy.deepcopy(discovery_report.get("issues", []))
        if isinstance(discovery_report.get("issues"), list)
        else []
    )
    raw_declarations = (
        declarations.get("declarations") if isinstance(declarations, Mapping) else None
    )
    declaration_rows = raw_declarations if isinstance(raw_declarations, list) else []
    raw_bindings = bindings.get("bindings") if isinstance(bindings, Mapping) else None
    bindings_rows = raw_bindings if isinstance(raw_bindings, list) else []
    node_map, templates = _test_index_maps(test_index_report)
    gaps: list[dict[str, Any]] = []
    report_errors = list(collection_errors or [])
    if report_errors:
        for index, reason in enumerate(report_errors):
            gaps.append(
                _gap(
                    "invalid-collection-report",
                    reason,
                    candidate_id=candidate_id,
                    collection_error_index=index,
                )
            )

    if not isinstance(candidate_id, str) or not candidate_id:
        gaps.append(
            _gap(
                "invalid-candidate",
                "candidate record has no candidate_id",
                candidate_id=candidate_id,
            )
        )
    if not isinstance(declarations, Mapping) or not isinstance(
        declarations.get("declarations"), list
    ):
        gaps.append(
            _gap(
                "declarations-unavailable",
                "declarations.json has no declarations list",
                candidate_id=candidate_id,
            )
        )
    if not isinstance(bindings, Mapping) or not isinstance(bindings.get("bindings"), list):
        gaps.append(
            _gap(
                "bindings-unavailable",
                "reviewed-bindings.json has no bindings list",
                candidate_id=candidate_id,
            )
        )
    if not isinstance(test_index_report, Mapping):
        gaps.append(
            _gap(
                "test-index-unavailable",
                "static test-node index is unavailable",
                candidate_id=candidate_id,
            )
        )
    if not isinstance(raw_surfaces, list):
        gaps.append(
            _gap(
                "discovery-surfaces-unavailable",
                "discovery report has no surfaces list",
                candidate_id=candidate_id,
            )
        )
    if isinstance(candidate.get("candidate_issues"), list):
        for issue_index, issue in enumerate(candidate["candidate_issues"]):
            gaps.append(
                _gap(
                    "candidate-issue",
                    str(issue),
                    candidate_id=candidate_id,
                    issue_index=issue_index,
                )
            )
    if isinstance(test_index_report, Mapping):
        for issue_index, issue in enumerate(test_index_report.get("issues", [])):
            gaps.append(
                _gap(
                    "test-index-issue",
                    str(issue.get("message") or issue.get("reason") or issue)
                    if isinstance(issue, Mapping)
                    else "test index emitted a non-mapping issue",
                    candidate_id=candidate_id,
                    issue_index=issue_index,
                    test_index_issue=copy.deepcopy(issue),
                )
            )

    for index, issue in enumerate(discovery_issues):
        if isinstance(issue, Mapping):
            gaps.append(
                _gap(
                    "discovery-issue",
                    str(issue.get("message") or issue.get("reason") or "discovery issue"),
                    candidate_id=candidate_id,
                    surface_id=(issue.get("surface_ids") or [None])[0]
                    if isinstance(issue.get("surface_ids"), list)
                    else None,
                    discovery_issue=dict(issue),
                    issue_index=index,
                )
            )
        else:
            gaps.append(
                _gap(
                    "discovery-issue",
                    "discovery emitted a non-mapping issue",
                    candidate_id=candidate_id,
                    issue_index=index,
                )
            )

    surfaces_by_id: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    canonical_surfaces: list[Any] = []
    for row in surfaces:
        if isinstance(row, Mapping):
            item = dict(row)
            surface_id = item.get("surface_id")
            if isinstance(surface_id, str) and surface_id:
                item["declaration_hints"] = _surface_hints(surface_id, declaration_rows)
                surfaces_by_id[surface_id].append(item)
            else:
                gaps.append(
                    _gap(
                        "invalid-discovery-surface",
                        "surface has no non-empty surface_id",
                        candidate_id=candidate_id,
                        surface_index=len(canonical_surfaces),
                    )
                )
            if not isinstance(item.get("surface_kind"), str) or not item.get("surface_kind"):
                gaps.append(
                    _gap(
                        "invalid-canonical-surface",
                        "surface_kind must be a non-empty string",
                        candidate_id=candidate_id,
                        surface_id=surface_id,
                        surface_index=len(canonical_surfaces),
                    )
                )
            declared_source = item.get("declared_source")
            if (
                not isinstance(declared_source, list)
                or not declared_source
                or not all(
                    isinstance(value, str) and _SOURCE_REF.fullmatch(value.strip())
                    for value in declared_source
                )
            ):
                gaps.append(
                    _gap(
                        "invalid-canonical-surface",
                        "declared_source must contain file:line references",
                        candidate_id=candidate_id,
                        surface_id=surface_id,
                        surface_index=len(canonical_surfaces),
                    )
                )
            canonical_surfaces.append(item)
        else:
            canonical_surfaces.append(row)
            gaps.append(
                _gap(
                    "invalid-discovery-surface",
                    "discovery emitted a non-mapping surface row",
                    candidate_id=candidate_id,
                )
            )
    canonical_surfaces.sort(key=_surface_key)
    for surface_id, rows in sorted(surfaces_by_id.items()):
        if len(rows) > 1:
            gaps.append(
                _gap(
                    "duplicate-discovery-surface",
                    "surface_id is ambiguous in the composed discovery",
                    candidate_id=candidate_id,
                    surface_id=surface_id,
                    occurrences=len(rows),
                )
            )

    matched_declarations: set[str] = set()
    for row in canonical_surfaces:
        if not isinstance(row, Mapping):
            continue
        for hint in row.get("declaration_hints", []):
            if isinstance(hint, Mapping) and isinstance(hint.get("declaration_id"), str):
                matched_declarations.add(hint["declaration_id"])
    for index, declaration in enumerate(declaration_rows):
        if not isinstance(declaration, Mapping):
            gaps.append(
                _gap(
                    "malformed-declaration",
                    "declaration row is not a mapping",
                    candidate_id=candidate_id,
                    declaration_index=index,
                )
            )
            continue
        declaration_id = declaration.get("declaration_id")
        if not isinstance(declaration_id, str):
            gaps.append(
                _gap(
                    "malformed-declaration",
                    "declaration has no declaration_id",
                    candidate_id=candidate_id,
                    declaration_index=index,
                )
            )
        elif declaration_id not in matched_declarations:
            gaps.append(
                _gap(
                    "unmatched-declaration-selector",
                    "declaration selector has no discovered surface match; selector is only a hint",
                    candidate_id=candidate_id,
                    declaration_id=declaration_id,
                    source=declaration.get("source"),
                )
            )

    cases: list[dict[str, Any]] = []
    valid_bound_surface_ids: set[str] = set()
    binding_pairs = [
        (index, row) for index, row in enumerate(bindings_rows) if isinstance(row, Mapping)
    ]
    binding_counts = Counter(_canonical(binding) for _, binding in binding_pairs)
    for binding_index, binding in binding_pairs:
        if binding_counts[_canonical(binding)] > 1:
            gaps.append(
                _gap(
                    "duplicate-binding",
                    "identical reviewed binding rows are ambiguous",
                    candidate_id=candidate_id,
                    surface_id=binding.get("surface_id"),
                    binding_index=binding_index,
                )
            )
    for index, binding in enumerate(bindings_rows):
        if not isinstance(binding, Mapping):
            gaps.append(
                _gap(
                    "malformed-binding",
                    "binding row is not a mapping",
                    candidate_id=candidate_id,
                    binding_index=index,
                )
            )
    for binding_index, binding in sorted(binding_pairs, key=_binding_sort_key):
        case, binding_gaps = _binding_case(
            root,
            binding,
            binding_index,
            candidate_id,
            [row for row in canonical_surfaces if isinstance(row, Mapping)],
            node_map,
            templates,
            collection_report,
            report_errors,
        )
        gaps.extend(binding_gaps)
        if case is not None:
            cases.append(case)
            valid_bound_surface_ids.add(str(case["surface_id"]))

    for row_index, row in enumerate(canonical_surfaces):
        if not isinstance(row, Mapping) or not isinstance(row.get("surface_id"), str):
            continue
        surface_id = str(row["surface_id"])
        if surface_id in valid_bound_surface_ids:
            gaps.append(
                _gap(
                    "journey-coverage-unreviewed",
                    "source-reviewed binding does not establish coverage for every journey, scenario, lane, or side effect",
                    candidate_id=candidate_id,
                    surface_id=surface_id,
                )
            )
            continue
        case_id = _case_id(
            "CASE-SURFACE",
            {
                "surface_id": surface_id,
                "origin": row.get("origin"),
                "source": row.get("source"),
                "row_index": row_index,
            },
        )
        cases.append(
            _draft_case(
                case_id=case_id,
                surface_id=surface_id,
                applicability="required",
                scenario_id="draft:unreviewed-surface-requirement",
                required_lanes=[],
                test_node_ids=[],
                expected="surface-specific positive, negative, lifecycle, cleanup, and journey oracles require review",
                draft_kind="unreviewed_surface_requirement",
                candidate_id=candidate_id,
            )
        )
        gaps.append(
            _gap(
                "unreviewed-surface",
                "no validated source-reviewed binding covers this surface; draft requirement is not coverage",
                candidate_id=candidate_id,
                surface_id=surface_id,
            )
        )

    cases.sort(key=lambda case: str(case.get("case_id", "")))
    gaps = _finalize_gaps(gaps)
    matrix: dict[str, Any] = {
        "schema_version": MATRIX_SCHEMA_VERSION,
        "dataset_schema_version": JSONL_SCHEMA_VERSION,
        "candidate_id": candidate_id,
        "candidate": candidate,
        "surfaces": canonical_surfaces,
        "discovery_issues": discovery_issues,
        "source_review": copy.deepcopy(discovery_report.get("source_review"))
        if isinstance(discovery_report, Mapping)
        else None,
        "declarations": copy.deepcopy(declarations),
        "cases": cases,
        "dispositions": [],
        "draft": {
            "status": "not_run",
            "release_claim": False,
            "rendering_claim": "draft matrix only; no acceptance or readiness claim",
            "runtime_collection_verified": False,
            "gap_ledger": "gap-ledger.json",
        },
    }
    matrix["draft_validation_errors"] = evidence.validate(matrix, discovery_report, release=False)
    ledger = {
        "schema_version": GAP_SCHEMA_VERSION,
        "candidate_id": candidate_id,
        "status": "open" if gaps else "empty",
        "release_claim": False,
        "gaps": gaps,
        "summary": dict(sorted(Counter(str(gap.get("kind")) for gap in gaps).items())),
    }
    return matrix, ledger


def _ensure_external_output(root: Path, output_dir: str | Path) -> Path:
    requested = Path(output_dir).expanduser()
    if not requested.is_absolute():
        requested = Path.cwd() / requested
    if os.path.lexists(requested):
        raise MatrixAssemblyError(
            "output_dir must be a new path; pre-existing directories and symlinks are refused"
        )
    output = requested.resolve()
    try:
        output.relative_to(root)
    except ValueError:
        pass
    else:
        raise MatrixAssemblyError("output_dir must resolve outside the candidate root")
    try:
        output.mkdir(parents=True, mode=0o700, exist_ok=False)
    except FileExistsError as exc:
        raise MatrixAssemblyError(
            "output_dir must be a new path; it was created concurrently"
        ) from exc
    output.chmod(0o700, follow_symlinks=False)
    return output


def _write_exclusive(path: Path, payload: str) -> None:
    """Create one private regular output file without following or replacing links."""
    file_descriptor: int | None = None
    try:
        file_descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        if not stat.S_ISREG(os.fstat(file_descriptor).st_mode):
            raise MatrixAssemblyError(f"output path is not a regular file: {path}")
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            file_descriptor = None
            handle.write(payload)
    except FileExistsError as exc:
        raise MatrixAssemblyError(f"output file already exists: {path}") from exc
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)


def _write_json(path: Path, payload: Any) -> None:
    _write_exclusive(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _jsonl_records(matrix: Mapping[str, Any], ledger: Mapping[str, Any]) -> list[dict[str, Any]]:
    candidate_id = matrix.get("candidate_id")
    records: list[dict[str, Any]] = [
        {
            "record_type": "header",
            "schema_version": JSONL_SCHEMA_VERSION,
            "candidate_id": candidate_id,
            "matrix_schema_version": matrix.get("schema_version"),
            "release_claim": False,
        },
    ]
    for surface in matrix.get("surfaces", []):
        records.append(
            {
                "record_type": "surface",
                "schema_version": JSONL_SCHEMA_VERSION,
                "candidate_id": candidate_id,
                "surface": surface,
            }
        )
    for case in matrix.get("cases", []):
        records.append(
            {
                "record_type": "case",
                "schema_version": JSONL_SCHEMA_VERSION,
                "candidate_id": candidate_id,
                "case": case,
            }
        )
    for gap in ledger.get("gaps", []):
        records.append(
            {
                "record_type": "gap",
                "schema_version": JSONL_SCHEMA_VERSION,
                "candidate_id": candidate_id,
                "gap": gap,
            }
        )
    return records


def _render(matrix: Mapping[str, Any], ledger: Mapping[str, Any]) -> str:
    text = evidence.render(dict(matrix))
    lines = [
        text.rstrip("\n"),
        "",
        "## Draft assembly gaps",
        "",
        f"- Candidate: `{matrix.get('candidate_id')}`",
        "- Gap count: " + str(len(ledger.get("gaps", []))),
        "- Gap ledger: `gap-ledger.json`",
        "- Source-reviewed bindings remain `not_run`; source review is not execution approval.",
        "- Runtime collection, provider calls, cleanup, and release readiness were not verified.",
        "",
    ]
    return "\n".join(lines)


def assemble(
    root: str | Path = ROOT,
    *,
    output_dir: str | Path,
    candidate_record: Mapping[str, Any] | None = None,
    discovery_report: Mapping[str, Any] | None = None,
    declarations_path: str | Path | None = None,
    bindings_path: str | Path | None = None,
    test_index_report: Mapping[str, Any] | None = None,
    collection_report: Mapping[str, Any] | None = None,
    collection_report_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build draft matrix outputs in an external caller-owned directory.

    A collection receipt is consumed only when passed explicitly as a mapping
    or ``collection_report_path``.  Paths are required to point at a fresh
    external receipt; repository-controlled absolute paths are never loaded
    implicitly.
    """
    if collection_report is not None and collection_report_path is not None:
        raise MatrixAssemblyError("provide collection_report or collection_report_path, not both")
    candidate_root = Path(root).resolve()
    output = _ensure_external_output(candidate_root, output_dir)
    record = (
        copy.deepcopy(dict(candidate_record))
        if candidate_record is not None
        else candidate_tool.build_candidate(candidate_root)
    )
    report = (
        copy.deepcopy(dict(discovery_report))
        if discovery_report is not None
        else discovery.build_report(candidate_root)
    )
    test_report = (
        copy.deepcopy(dict(test_index_report))
        if test_index_report is not None
        else test_index.build_report(candidate_root)
    )
    declarations, declaration_issue = _read_json_input(
        candidate_root,
        _input_path(candidate_root, declarations_path, candidate_root / DECLARATIONS_RELATIVE),
        "declarations",
    )
    bindings, binding_issue = _read_json_input(
        candidate_root,
        _input_path(candidate_root, bindings_path, candidate_root / BINDINGS_RELATIVE),
        "reviewed bindings",
    )
    if declaration_issue is not None:
        declarations = None
    if binding_issue is not None:
        bindings = None
    loaded_collection: Mapping[str, Any] | None = None
    collection_errors: list[str] = []
    if collection_report is not None:
        loaded_collection, collection_errors = _collection_report_validation(
            candidate_root, collection_report
        )
    elif collection_report_path is not None:
        raw_collection, collection_issue = _read_external_collection_input(
            candidate_root, Path(collection_report_path)
        )
        if collection_issue is not None:
            collection_errors = [collection_issue["reason"]]
        else:
            loaded_collection, collection_errors = _collection_report_validation(
                candidate_root, raw_collection
            )
    matrix, ledger = _assemble(
        root=candidate_root,
        candidate_record=record,
        discovery_report=report,
        declarations=declarations,
        bindings=bindings,
        test_index_report=test_report,
        collection_report=loaded_collection,
        collection_errors=collection_errors,
    )
    if declaration_issue is not None:
        ledger["gaps"] = _finalize_gaps(
            [
                *ledger["gaps"],
                _gap(
                    declaration_issue["kind"],
                    declaration_issue["reason"],
                    candidate_id=matrix.get("candidate_id"),
                    source=declaration_issue.get("source"),
                ),
            ]
        )
    if binding_issue is not None:
        ledger["gaps"] = _finalize_gaps(
            [
                *ledger["gaps"],
                _gap(
                    binding_issue["kind"],
                    binding_issue["reason"],
                    candidate_id=matrix.get("candidate_id"),
                    source=binding_issue.get("source"),
                ),
            ]
        )
    ledger["status"] = "open" if ledger["gaps"] else "empty"
    ledger["summary"] = dict(
        sorted(Counter(str(gap.get("kind")) for gap in ledger["gaps"]).items())
    )
    candidate_projection = matrix["candidate"]
    _write_json(output / "candidate.json", candidate_projection)
    _write_json(output / "candidate-snapshot.json", record)
    _write_json(output / "acceptance-evidence.v1.json", matrix)
    _write_exclusive(
        output / "acceptance.v1.jsonl",
        "".join(_canonical(record) + "\n" for record in _jsonl_records(matrix, ledger)),
    )
    _write_json(output / "gap-ledger.json", ledger)
    _write_exclusive(output / "acceptance-matrix.md", _render(matrix, ledger))
    return {
        "candidate_id": matrix.get("candidate_id"),
        "output_dir": str(output),
        "matrix": matrix,
        "gap_ledger": ledger,
        "files": {
            "candidate": str(output / "candidate.json"),
            "candidate_snapshot": str(output / "candidate-snapshot.json"),
            "matrix_json": str(output / "acceptance-evidence.v1.json"),
            "matrix_jsonl": str(output / "acceptance.v1.jsonl"),
            "markdown": str(output / "acceptance-matrix.md"),
            "gap_ledger": str(output / "gap-ledger.json"),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="release-acceptance-matrix")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--declarations", type=Path, default=None)
    parser.add_argument("--bindings", type=Path, default=None)
    parser.add_argument("--collection-report", type=Path, default=None)
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    try:
        result = assemble(
            args.root,
            output_dir=args.output_dir,
            declarations_path=args.declarations,
            bindings_path=args.bindings,
            collection_report_path=args.collection_report,
        )
    except (MatrixAssemblyError, OSError, ValueError) as exc:
        print(f"matrix: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "candidate_id": result["candidate_id"],
                "output_dir": result["output_dir"],
                "files": result["files"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
