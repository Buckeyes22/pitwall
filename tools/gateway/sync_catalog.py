"""Compatibility shim over ``pitwall.gateway_catalog.sync``.

Keeps ``python -m tools.gateway.sync_catalog`` and ``python tools/gateway/sync_catalog.py``
working; the implementation ships in the package so ``pitwall gateway sync`` runs when
installed. ``REPO_ROOT`` here is honoured by ``main`` so callers can point at a checkout.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT_HINT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT_HINT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT_HINT))

from pitwall.gateway_catalog import sync as _sync  # noqa: E402  # reason: needs sys.path bootstrap
from pitwall.gateway_catalog.sync import (  # noqa: E402  # reason: needs sys.path bootstrap
    FORK_BASE_URL,
    KNOWN_UNREACHABLE,
    CatalogArtifacts,
    PoolTotals,
    apply_verdicts,
    dedupe_pool_totals,
    load_rows,
    registry_covered_count,
    transform,
    write_artifacts,
)

__all__ = [
    "DEFAULT_SEED_PATH",
    "FORK_BASE_URL",
    "KNOWN_UNREACHABLE",
    "REPO_ROOT",
    "CatalogArtifacts",
    "PoolTotals",
    "apply_verdicts",
    "dedupe_pool_totals",
    "load_rows",
    "main",
    "registry_covered_count",
    "transform",
    "write_artifacts",
]

REPO_ROOT = _REPO_ROOT_HINT
DEFAULT_SEED_PATH = REPO_ROOT / "seed" / "gateway-providers.yaml"


def main(argv: list[str] | None = None) -> int:
    return _sync.main(argv, repo_root=REPO_ROOT)


if __name__ == "__main__":
    sys.exit(main())
