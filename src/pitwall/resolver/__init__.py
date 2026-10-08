"""Runtime capability resolver types.

The resolver maps consumer capability requests to concrete RunPod providers
using the four-stage routing algorithm (hard constraints, health gate,
hint-based ranking, fallback chain selection).

``resolver.service`` imports ``pitwall.routing``, and routing imports provider adapters that
import ``resolver.provider_urls``. The service names are therefore loaded on first access so
importing the URL helpers or the exception types never loads routing.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

from pitwall.resolver.exceptions import (
    CapabilityDisabledError,
    CapabilityNotFoundError,
    NoHealthyProviderError,
    ProviderExhaustedError,
    ProviderNotFoundError,
    ResolverError,
)
from pitwall.resolver.provider_urls import (
    lb_url,
    openai_base_url,
    provider_url,
    public_endpoint_url,
    queue_url,
)
from pitwall.resolver.result import (
    ResolutionFailure,
    ResolutionResult,
    ResolvedProvider,
)

_SERVICE_NAMES = frozenset(
    {
        "CapabilityRepositoryLike",
        "ProviderRepositoryLike",
        "Stage12Resolution",
        "resolve_capability",
        "resolve_capability_record",
        "select_stage12_provider",
    }
)


def __getattr__(name: str) -> Any:
    if name in _SERVICE_NAMES:
        value = getattr(import_module("pitwall.resolver.service"), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "CapabilityDisabledError",
    "CapabilityNotFoundError",
    "CapabilityRepositoryLike",
    "NoHealthyProviderError",
    "ProviderRepositoryLike",
    "ProviderExhaustedError",
    "ProviderNotFoundError",
    "ResolvedProvider",
    "ResolutionFailure",
    "ResolutionResult",
    "ResolverError",
    "Stage12Resolution",
    "lb_url",
    "openai_base_url",
    "provider_url",
    "public_endpoint_url",
    "queue_url",
    "resolve_capability",
    "resolve_capability_record",
    "select_stage12_provider",
]
