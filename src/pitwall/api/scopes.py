"""Bearer-token API scopes and the scoped-token configuration parser.

Kept free of app import side effects so configuration checks can validate
``PITWALL_API_SCOPED_TOKENS`` without starting the API.

Job payload visibility: ``GET /v1/jobs/{id}`` and ``GET /v1/jobs/{id}/result``
return the stored job ``input`` and ``result`` only to tokens holding
``PAYLOAD_READ_SCOPE`` (the scope required to create jobs, ``spend``). Tokens
that hold only ``read`` get job metadata with ``input`` and ``result`` null.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

READ_SCOPE = "read"
SPEND_SCOPE = "spend"
LEASE_MUTATION_SCOPE = "lease:mutate"
WEBHOOK_ADMIN_SCOPE = "webhook:admin"
SERVER_ADMIN_SCOPE = "server:admin"
ALL_API_SCOPES = frozenset(
    {
        READ_SCOPE,
        SPEND_SCOPE,
        LEASE_MUTATION_SCOPE,
        WEBHOOK_ADMIN_SCOPE,
        SERVER_ADMIN_SCOPE,
    }
)

#: Scope that may read stored job input and result: the scope that creates jobs.
PAYLOAD_READ_SCOPE = SPEND_SCOPE
#: ASGI scope key where the bearer middleware records the scopes it granted.
GRANTED_SCOPES_KEY = "pitwall.granted_scopes"


def granted_scopes(asgi_scope: Mapping[str, object]) -> frozenset[str]:
    """Scopes the middleware granted this request; empty when none were recorded."""
    granted = asgi_scope.get(GRANTED_SCOPES_KEY)
    return granted if isinstance(granted, frozenset) else frozenset()


def can_read_payloads(asgi_scope: Mapping[str, object]) -> bool:
    """Whether this request may see stored job input and result."""
    return PAYLOAD_READ_SCOPE in granted_scopes(asgi_scope)


def parse_scoped_tokens(raw: str) -> list[tuple[str, frozenset[str]]]:
    """Parse ``PITWALL_API_SCOPED_TOKENS``: a JSON object of token to explicit scope list."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("PITWALL_API_SCOPED_TOKENS must be a JSON object") from exc
    if not isinstance(payload, Mapping) or not payload:
        raise ValueError("PITWALL_API_SCOPED_TOKENS must be a non-empty JSON object")
    entries: list[tuple[str, frozenset[str]]] = []
    for token, raw_scopes in payload.items():
        if not isinstance(token, str) or not token:
            raise ValueError("scoped bearer tokens must be non-empty strings")
        if not isinstance(raw_scopes, list) or not raw_scopes:
            raise ValueError("each scoped bearer token must grant a non-empty scope list")
        if not all(isinstance(scope, str) for scope in raw_scopes):
            raise ValueError("API scope names must be strings")
        scopes = frozenset(raw_scopes)
        unknown = scopes - ALL_API_SCOPES
        if unknown:
            raise ValueError(f"unknown API scope(s): {', '.join(sorted(unknown))}")
        entries.append((token, scopes))
    return entries


__all__ = [
    "ALL_API_SCOPES",
    "GRANTED_SCOPES_KEY",
    "LEASE_MUTATION_SCOPE",
    "PAYLOAD_READ_SCOPE",
    "READ_SCOPE",
    "SERVER_ADMIN_SCOPE",
    "SPEND_SCOPE",
    "WEBHOOK_ADMIN_SCOPE",
    "can_read_payloads",
    "granted_scopes",
    "parse_scoped_tokens",
]
