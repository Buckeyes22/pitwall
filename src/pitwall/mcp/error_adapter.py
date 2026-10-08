"""Error adapter — map service exceptions to structured MCP errors.

This module converts service-layer exceptions (``PitwallApiError``,
``ResolverError``, ``LeaseTransitionError``) into MCP ``MCPError`` responses
so that MCP clients receive the same structured error codes that the REST API
uses.

Public API::

    from pitwall.mcp.error_adapter import adapt_error

    try:
        result = some_service_function()
    except Exception as e:
        raise adapt_error(e) from e

The ``MCPError.error.data`` field contains a dict with at least::

    {
        "error": "<error_code>",       # same string code as REST API
        ...                             # additional fields from the exception
    }

Pitwall integer codes are application-defined (``-31000`` to ``-31010``), outside the
JSON-RPC reserved range.
``pitwall.mcp.error_codes`` partitions the string ``error_code`` vocabulary
into five classes (``-31001`` authn/z, ``-31002`` budget/spend, ``-31003``
validation, ``-31004`` upstream/provider, ``-31005`` conflict/state) with
``-31000`` as the unmapped fallback. The specific string code is still
carried in ``error.data["error"]``. ``-31010`` belongs to ``pitwall mcp relay``.
"""

from __future__ import annotations

from typing import Any, cast

from mcp.shared.exceptions import MCPError

PITWALL_ERROR_CODE_BASE = -31000

_API_ERROR_CODE_TO_MCP_CODE: dict[str, int] = {}


def _extract_error_data(exc: Exception) -> dict[str, Any]:
    """Extract structured error data from a service exception.

    Returns a dict with at least ``{"error": "<code>"}``.
    Subclasses may add more fields (e.g. ``name``, ``id``).
    """
    if hasattr(exc, "to_response_body"):
        return cast(dict[str, Any], exc.to_response_body())
    if hasattr(exc, "to_dict"):
        return cast(dict[str, Any], exc.to_dict())
    if hasattr(exc, "error_code"):
        return {"error": exc.error_code}
    if hasattr(exc, "code"):
        candidate = exc.code
        if isinstance(candidate, str) and candidate:
            return {"error": candidate}
    return {"error": "internal_error"}


def _get_error_code(exc: Exception) -> str:
    """Return the error code string for an exception."""
    if hasattr(exc, "error_code"):
        return cast(str, exc.error_code)
    if hasattr(exc, "code"):
        candidate = exc.code
        if isinstance(candidate, str) and candidate:
            return candidate
    return "internal_error"


def adapt_error(exc: Exception) -> MCPError:
    """Convert a service exception to an ``MCPError`` with structured error data.

    Args:
        exc: Any exception, typically a ``PitwallApiError``,
            ``ResolverError``, or ``LeaseTransitionError``.

    Returns:
        An ``MCPError`` with ``ErrorData`` containing the same
        ``error_code`` string used by the REST API.
    """
    error_code = _get_error_code(exc)
    data = _extract_error_data(exc)
    mcp_code = _API_ERROR_CODE_TO_MCP_CODE.get(error_code, PITWALL_ERROR_CODE_BASE)
    message = str(exc) or error_code
    return MCPError(code=mcp_code, message=message, data=data)


def register_error_code(error_code: str, mcp_code: int) -> None:
    """Register a mapping from a string error code to an MCP integer code.

    Args:
        error_code: The string error code (e.g. ``"capability_not_found"``).
        mcp_code: The Pitwall integer code (``-31000`` to ``-31005``).
    """
    _API_ERROR_CODE_TO_MCP_CODE[error_code] = mcp_code


# Bootstrap the class partition so any ``adapt_error`` caller gets the
# populated map without having to import ``error_codes`` explicitly.
# The import is deferred to avoid a circular dependency at load time.
try:  # pragma: no cover - exercised by integration tests
    from pitwall.mcp.error_codes import register_error_codes as _bootstrap_error_codes

    _bootstrap_error_codes()
except ImportError:
    pass


__all__ = [
    "adapt_error",
    "register_error_code",
    "PITWALL_ERROR_CODE_BASE",
]
