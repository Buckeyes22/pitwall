"""Task 2 §12.2 — error-code partition bootstrap."""

from __future__ import annotations

from pitwall.api.exceptions import (
    CapabilityDisabled,
    CapabilityNotFound,
    ProviderConflict,
    ServeBudgetExhausted,
)
from pitwall.mcp.error_adapter import PITWALL_ERROR_CODE_BASE, adapt_error
from pitwall.mcp.error_codes import (
    AUTHZ,
    BUDGET,
    CONFLICT,
    UPSTREAM,
    VALIDATION,
    register_error_codes,
)


def test_error_codes_are_partitioned() -> None:
    register_error_codes()

    # authn/z -31001
    err = adapt_error(CapabilityDisabled("cap-x"))
    assert err.error.code == AUTHZ
    assert err.error.data is not None
    assert err.error.data["error"] == "capability_disabled"

    # budget/spend -31002
    err = adapt_error(ServeBudgetExhausted(reason="exhausted", snapshot={}))
    assert err.error.code == BUDGET
    assert err.error.data is not None
    assert err.error.data["error"] == "budget_exhausted"

    # validation -31003
    err = adapt_error(CapabilityNotFound("missing"))
    assert err.error.code == VALIDATION

    # conflict/state -31005
    err = adapt_error(ProviderConflict("dup"))
    assert err.error.code == CONFLICT

    # upstream is also covered via a RunPodControlPlaneError shape (has .code)
    class _FakeUpstream(Exception):
        code = "provider_error"

    err = adapt_error(_FakeUpstream("upstream"))
    assert err.error.code == UPSTREAM

    # fallback -31000 for unmapped
    class _Unknown(Exception):
        error_code = "internal_error"

    err = adapt_error(_Unknown("boom"))
    assert err.error.code == PITWALL_ERROR_CODE_BASE
    assert err.error.data["error"] == "internal_error"


def test_error_codes_auto_bootstrap_on_import() -> None:
    # importing error_adapter alone should have populated the map
    from pitwall.mcp import error_adapter as ea

    # after import, a validation code should already be partitioned without explicit register call
    err = ea.adapt_error(CapabilityNotFound("x"))
    assert err.error.code == VALIDATION


def test_register_error_codes_is_idempotent() -> None:
    register_error_codes()
    register_error_codes()
    err = adapt_error(CapabilityDisabled("y"))
    assert err.error.code == AUTHZ


def test_pitwall_codes_sit_outside_the_jsonrpc_reserved_range() -> None:
    from pitwall.mcp import error_codes
    from pitwall.mcp.error_adapter import PITWALL_ERROR_CODE_BASE

    codes = {
        PITWALL_ERROR_CODE_BASE,
        error_codes.AUTHZ,
        error_codes.BUDGET,
        error_codes.VALIDATION,
        error_codes.UPSTREAM,
        error_codes.CONFLICT,
    }
    assert codes == {-31000, -31001, -31002, -31003, -31004, -31005}
    assert all(not (-32768 <= code <= -32000) for code in codes)


def test_relay_code_is_registered_and_does_not_collide() -> None:
    from pitwall.mcp import error_codes, relay

    class_codes = {
        error_codes.AUTHZ,
        error_codes.BUDGET,
        error_codes.VALIDATION,
        error_codes.UPSTREAM,
        error_codes.CONFLICT,
    }
    assert relay.ERROR_CODE == error_codes.RELAY == -31010
    assert error_codes.RELAY not in class_codes | {-31000}
    assert error_codes.RELAY not in error_codes._CODE_MAP.values()
    assert not (-32768 <= error_codes.RELAY <= -32000)
