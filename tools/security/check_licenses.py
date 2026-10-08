"""Inventory the installed runtime graph and enforce the release license policy."""

from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from importlib.metadata import Distribution, PackageNotFoundError, distribution
from pathlib import Path
from typing import Any

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "tools" / "security" / "license-policy.json"

CLASSIFIER_LICENSES = {
    "Apache Software License": "Apache-2.0",
    "BSD License": "BSD",
    "ISC License (ISCL)": "ISC",
    "MIT License": "MIT",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "Python Software Foundation License": "PSF-2.0",
}
RAW_LICENSES = {
    "Apache 2.0": "Apache-2.0",
    "Apache License 2.0": "Apache-2.0",
    "MIT License": "MIT",
}


def _license(dist: Distribution) -> str:
    expression = dist.metadata.get("License-Expression")
    if expression:
        return expression.strip()
    raw = (dist.metadata.get("License") or "").strip()
    if raw and raw != "Dual License" and len(raw) <= 120 and "\n" not in raw:
        return RAW_LICENSES.get(raw, raw)
    classifier_values: list[str] = []
    for classifier in dist.metadata.get_all("Classifier", []):
        prefix = "License :: OSI Approved :: "
        if classifier.startswith(prefix):
            classifier_values.append(
                CLASSIFIER_LICENSES.get(classifier.removeprefix(prefix), classifier)
            )
    if classifier_values:
        return " OR ".join(classifier_values)
    return "UNKNOWN"


def runtime_graph(root_name: str, extras: frozenset[str] = frozenset()) -> list[Distribution]:
    """Resolve installed dependencies from one root, propagating requested extras transitively."""

    start = (canonicalize_name(root_name), frozenset(canonicalize_name(e) for e in extras))
    queue = deque([start])
    visited: set[tuple[str, frozenset[str]]] = set()
    found: dict[str, Distribution] = {}
    while queue:
        name, active = queue.popleft()
        if (name, active) in visited:
            continue
        visited.add((name, active))
        try:
            dist = distribution(name)
        except PackageNotFoundError as exc:
            raise RuntimeError(f"dependency is not installed: {name}") from exc
        found[name] = dist
        for raw_requirement in dist.requires or []:
            requirement = Requirement(raw_requirement)
            if requirement.marker is not None and not any(
                requirement.marker.evaluate({"extra": extra}) for extra in ("", *sorted(active))
            ):
                continue
            queue.append(
                (
                    canonicalize_name(requirement.name),
                    frozenset(canonicalize_name(e) for e in requirement.extras),
                )
            )
    return sorted(found.values(), key=lambda item: canonicalize_name(item.metadata["Name"]))


def npm_lock_rows(lock: dict[str, Any]) -> list[dict[str, str]]:
    """One row per installed package in an npm lockfile v3."""

    rows: list[dict[str, str]] = []
    for path, entry in sorted(lock["packages"].items()):
        if not path or entry.get("link"):
            continue
        name = entry.get("name") or path.rsplit("node_modules/", 1)[1]
        license_value = entry.get("license")
        rows.append(
            {
                "name": name,
                "version": str(entry.get("version", "")),
                "license": str(license_value) if license_value else "UNKNOWN",
            }
        )
    return rows


def evaluate(
    rows: list[dict[str, str]], policy: dict[str, Any], profile: str = "base"
) -> list[str]:
    errors: list[str] = []
    allowed = tuple(policy["allowed_license_terms"])
    denied = tuple(policy["denied_license_terms"])
    merged = {
        # The pinned Python reviews describe the Python graph; the npm graph has its own.
        **({} if profile == "npm" else policy["review_required_packages"]),
        **policy.get("profile_review_required", {}).get(profile, {}),
    }
    # npm nests several versions of one package, so its reviews are keyed "name@version".
    npm = profile == "npm"
    review = {
        name.lower() if npm else canonicalize_name(name): {
            "version": str(expected["version"]),
            "license": str(expected["license"]),
            # npm locks sometimes omit a license on one copy of a package and not another.
            "accepted": {
                str(expected[key]) for key in ("license", "verified_license") if key in expected
            },
        }
        for name, expected in merged.items()
    }
    seen_review: set[str] = set()
    for row in rows:
        name: str = canonicalize_name(row["name"])
        if npm:
            name = f"{row['name'].lower()}@{row['version']}"
        license_value = row["license"]
        if name in review:
            seen_review.add(name)
            expected = review[name]
            if row["version"] != expected["version"]:
                errors.append(
                    f"{name}: version changed from reviewed {expected['version']!r} "
                    f"to {row['version']!r}"
                )
            if license_value not in expected["accepted"]:
                errors.append(
                    f"{name} {row['version']}: license changed from reviewed "
                    f"{expected['license']!r} to {license_value!r}"
                )
            continue
        if any(term in license_value for term in denied):
            errors.append(f"{name} {row['version']}: denied license {license_value!r}")
            continue
        if license_value == "UNKNOWN" or not any(term in license_value for term in allowed):
            errors.append(
                f"{name} {row['version']}: unknown or unapproved license {license_value!r}"
            )
    missing = sorted(set(review) - seen_review)
    errors.extend(
        f"review-required package missing from {profile} graph: {name}" for name in missing
    )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="pitwall")
    parser.add_argument("--extra", action="append", default=[])
    parser.add_argument("--npm-lock", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    if args.npm_lock is not None:
        profile = "npm"
        rows = npm_lock_rows(json.loads(args.npm_lock.read_text(encoding="utf-8")))
    else:
        profile = "+".join(sorted(args.extra)) or "base"
        rows = [
            {
                "name": dist.metadata["Name"],
                "version": dist.version,
                "license": _license(dist),
            }
            for dist in runtime_graph(args.root, frozenset(args.extra))
        ]
    errors = evaluate(rows, policy, profile)
    report = {
        "root": args.root,
        "profile": profile,
        "status": "pass" if not errors else "fail",
        "legal_approval": "project-owner-approved exact graph; see docs/legal/transitive-license-review.md",
        "packages": rows,
        "errors": errors,
    }
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    for error in errors:
        print(f"license policy failed: {error}", file=sys.stderr)
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
