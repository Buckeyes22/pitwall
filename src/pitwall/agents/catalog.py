"""Load and validate the lightweight open-weight model catalog."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .resources import ResourceError, read_resource_json


class CatalogError(ValueError):
    """Raised when the packaged model catalog violates the runtime contract."""


_CATALOG_ID = re.compile(r"[a-z0-9][a-z0-9.-]*")
_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
_HF_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_REQUIRED = {
    "displayName",
    "vendor",
    "modelId",
    "hfRepo",
    "license",
    "contextWindow",
    "reasoningControl",
    "modelCardUrl",
}
_OPTIONAL = {"family", "servingHint"}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CatalogError(message)


def known_family_ids(registry: Mapping[str, Any]) -> set[str]:
    """Family ids a catalog entry may reference: modelFamilies plus every harness routeFamily."""
    ids = set(registry.get("modelFamilies", {}))
    for harness in registry.get("harnesses", {}).values():
        ids.update(family["id"] for family in harness.get("routeFamilies", []))
    return ids


def validate_catalog(data: Any, *, registry: Mapping[str, Any]) -> None:
    _require(isinstance(data, dict), "catalog must be an object")
    _require(data.get("schemaVersion") == 1, "catalog schemaVersion must be 1")
    _require(
        set(data) == {"schemaVersion", "sourceVerifiedOn", "models"},
        "catalog has unexpected top-level fields",
    )
    verified = data.get("sourceVerifiedOn")
    _require(
        isinstance(verified, str) and _DATE.fullmatch(verified) is not None,
        "catalog sourceVerifiedOn must be YYYY-MM-DD",
    )
    models = data.get("models")
    _require(isinstance(models, dict), "catalog models must be an object")
    families = known_family_ids(registry)
    seen_model_ids: dict[str, str] = {}
    for catalog_id, entry in models.items():
        _require(
            isinstance(catalog_id, str) and _CATALOG_ID.fullmatch(catalog_id) is not None,
            f"invalid catalog id: {catalog_id!r}",
        )
        field = f"models.{catalog_id}"
        _require(isinstance(entry, dict), f"{field} must be an object")
        missing = _REQUIRED - set(entry)
        extra = set(entry) - _REQUIRED - _OPTIONAL
        _require(not missing, f"{field} is missing required fields: {', '.join(sorted(missing))}")
        _require(not extra, f"{field} has unknown fields: {', '.join(sorted(extra))}")
        for key in ("displayName", "vendor", "license", "reasoningControl"):
            _require(
                isinstance(entry[key], str) and bool(entry[key]),
                f"{field}.{key} must be a non-empty string",
            )
        _require(
            isinstance(entry["modelId"], str) and _MODEL_ID.fullmatch(entry["modelId"]) is not None,
            f"{field}.modelId is invalid",
        )
        _require(
            isinstance(entry["hfRepo"], str) and _HF_REPO.fullmatch(entry["hfRepo"]) is not None,
            f"{field}.hfRepo must be org/name",
        )
        window = entry["contextWindow"]
        _require(
            type(window) is int and window > 0, f"{field}.contextWindow must be a positive integer"
        )
        url = entry["modelCardUrl"]
        _require(
            isinstance(url, str) and url.startswith("https://"),
            f"{field}.modelCardUrl must be an https URL",
        )
        if "family" in entry:
            _require(
                entry["family"] in families,
                f"{field}.family references unknown family {entry['family']!r}",
            )
        if "servingHint" in entry:
            _require(
                isinstance(entry["servingHint"], str) and bool(entry["servingHint"]),
                f"{field}.servingHint must be a non-empty string",
            )
        owner = seen_model_ids.setdefault(entry["modelId"].lower(), catalog_id)
        _require(owner == catalog_id, f"{field}.modelId duplicates {owner}")


def load_catalog(path: Path | None = None, *, registry: Mapping[str, Any]) -> dict[str, Any]:
    if path is None:
        try:
            data = read_resource_json("config/model-catalog.json")
        except ResourceError as exc:
            raise CatalogError(str(exc)) from exc
    else:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CatalogError(f"cannot load model catalog {path}: {exc}") from exc
    validate_catalog(data, registry=registry)
    catalog: dict[str, Any] = data
    return catalog
