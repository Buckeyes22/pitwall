"""Alibaba Cloud Model Studio provider.

The catalog and OpenAPI modules are light; the adapter loads on first attribute access.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pitwall.providers.model_studio.adapter import (
        ModelStudioCredentials,
        ModelStudioInferenceResult,
        ModelStudioProvider,
        ModelStudioProviderError,
    )

__all__ = [
    "ModelStudioCredentials",
    "ModelStudioInferenceResult",
    "ModelStudioProvider",
    "ModelStudioProviderError",
]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module("pitwall.providers.model_studio.adapter"), name)
    globals()[name] = value
    return value
