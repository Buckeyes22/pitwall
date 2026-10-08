"""Feature-local MCP tools for the free-tier gateway catalog and quota snapshot.

Two read-only tools expose ADR 0007 catalog evidence and the live provider
quota snapshot without persisting or egressing:

- ``pitwall_gateway_catalog_read`` reads ``config/gateway-catalog.json`` and
  optionally filters by ToS verdict or routability; it never imports
  ``pitwall.cost`` or ``pitwall.routing`` and never writes.
- ``pitwall_quota_list`` delegates to ``QuotaRepository.list_all`` so MCP,
  REST, and the CLI share the same persisted provider-quota snapshot.

Both handlers are thin adapters; the canonical implementation lives in
``pitwall.db.quota_repository`` and the catalog JSON is produced by
``tools/gateway/sync_catalog.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pitwall.db import get_pool
from pitwall.db.quota_repository import QuotaRepository

_REPO_ROOT = Path(__file__).resolve().parents[4]
_CATALOG_PATH = _REPO_ROOT / "config" / "gateway-catalog.json"
_CATALOG_SOURCE = "config/gateway-catalog.json"
_ROUTABLE_FREE_TYPES = frozenset({"keyless"})
_BLOCKING_TOS = frozenset({"avoid", "unknown"})


def _catalog_root() -> dict[str, Any]:
    if not _CATALOG_PATH.exists():
        return {"providers": [], "totals": {}, "avoid_list": [], "curated_at": None}
    loaded: Any = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    if isinstance(loaded, dict):
        return loaded
    return {"providers": [], "totals": {}, "avoid_list": [], "curated_at": None}


def _row_is_routable(row: dict[str, Any]) -> bool:
    catalog = row.get("gateway", {}).get("catalog", {}) if isinstance(row, dict) else {}
    if not isinstance(catalog, dict):
        return False
    if catalog.get("eligibility_gate"):
        return False
    tos = str(catalog.get("tos", ""))
    if tos in _BLOCKING_TOS:
        return False
    free_type = str(catalog.get("free_type", ""))
    if free_type == "discontinued":
        return False
    return bool(catalog.get("hard_stop_guaranteed", False)) or free_type in _ROUTABLE_FREE_TYPES


async def pitwall_gateway_catalog_read(
    tos: str | None = None,
    routable_only: bool = False,
) -> dict[str, Any]:
    """Return the synced free-tier catalog as a read-only snapshot.

    The catalog file is the single source of truth produced by
    ``tools/gateway/sync_catalog.py``; this handler never writes, never imports
    ``pitwall.cost``, and never reaches the network.
    """
    payload = _catalog_root()
    providers: list[dict[str, Any]] = [
        provider for provider in payload.get("providers", []) if isinstance(provider, dict)
    ]
    if tos is not None:
        providers = [
            provider
            for provider in providers
            if provider.get("gateway", {}).get("catalog", {}).get("tos") == tos
        ]
    if routable_only:
        providers = [provider for provider in providers if _row_is_routable(provider)]
    return {
        "source": _CATALOG_SOURCE,
        "curated_at": payload.get("curated_at"),
        "totals": payload.get("totals", {}),
        "avoid_list": payload.get("avoid_list", []),
        "providers": providers,
    }


async def pitwall_quota_list() -> dict[str, Any]:
    """Return the persisted provider-quota snapshot via ``QuotaRepository``."""
    repo = QuotaRepository(await get_pool())
    records = await repo.list_all()
    quotas: list[dict[str, Any]] = []
    for record in records:
        snapshot = record.to_dict()
        budget = record.budget_units
        used = record.used_units
        if budget is None or budget == 0:
            headroom = 1.0
        else:
            headroom = max(0.0, min(1.0, float((budget - used) / budget)))
        snapshot["headroom"] = headroom
        quotas.append(snapshot)
    return {"quotas": quotas}


__all__ = ["pitwall_gateway_catalog_read", "pitwall_quota_list"]
