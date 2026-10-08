"""Compatibility shim: the schema lives in ``pitwall.gateway_catalog.schema``."""

from __future__ import annotations

from pitwall.gateway_catalog.schema import (
    FREE_TYPES,
    ONE_TIME,
    RECURRING_CREDIT,
    STEADY_MONTHLY,
    TOS_VERDICTS,
    UNCAPPED,
    CatalogRow,
)

__all__ = [
    "FREE_TYPES",
    "ONE_TIME",
    "RECURRING_CREDIT",
    "STEADY_MONTHLY",
    "TOS_VERDICTS",
    "UNCAPPED",
    "CatalogRow",
]
