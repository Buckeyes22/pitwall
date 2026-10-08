"""Error-code partition bootstrap — maps string error codes to class MCP codes.

This module is the partition described in §12.2 of the orchestrator channel
plan.  It maps the existing string ``error_code`` vocabulary (enumerated from
``src/pitwall/api/exceptions.py``, ``src/pitwall/resolver/exceptions.py``,
``src/pitwall/runpod_control_plane.py``, ``src/pitwall/runpod_files.py``,
``src/pitwall/models/errors.py`` and
the cost/lease helpers) to five JSON-RPC class codes via the existing
``register_error_code()`` registry in ``pitwall.mcp.error_adapter``.

Class codes
-----------
- ``-31001`` — authn/z: disabled/credential problems.
- ``-31002`` — budget/spend: monthly/per-request/sub-budget, price, cap, kill-switch.
- ``-31003`` — validation: malformed or unprocessable client input (400/422).
- ``-31004`` — upstream/provider: RunPod/provider, network, or persistence failure (502/503/504).
- ``-31005`` — conflict/state: duplicate or incompatible lifecycle state (409).
- ``-31010`` — relay: ``pitwall mcp relay`` answers a request it cannot deliver (the server
  restarted mid-request, or is not ready); ``data.retryable`` is true. Not a string-code
  class, so ``_CODE_MAP`` carries no entry for it.

``-31000`` (``PITWALL_ERROR_CODE_BASE``) remains the fallback for any
unmapped code (e.g. ``internal_error``) and for synthetic ``MCPError`` shapes.
``ErrorData.data`` is unchanged — it still carries the original string
``{"error": "<code>", ...}`` from ``to_response_body()`` / ``to_dict()``.

These numbers classify errors inside Pitwall; ``safe_boundary`` delivers every tool
failure to MCP clients as an ``isError`` result carrying only the string ``error`` code.
"""

from __future__ import annotations

# Class codes — application-defined, outside the JSON-RPC reserved range
# (-32768..-32000), per the MCP 2026-07-28 error-code allocation policy.
AUTHZ = -31001
BUDGET = -31002
VALIDATION = -31003
UPSTREAM = -31004
CONFLICT = -31005
RELAY = -31010

# Mapping from string error_code -> MCP integer code.
# Do not invent new strings: every key appears as ``error_code = "..."``
# (or ``self.code = "..."`` for RunPodControlPlaneError) in the sources
# listed above.
_CODE_MAP: dict[str, int] = {
    # -31001 authn/z — capability is disabled or a credential reference is missing.
    "capability_disabled": AUTHZ,
    "credential_reference_unset": AUTHZ,
    # -31002 budget/spend — price, cap, or budget admission refused.
    "budget_exhausted": BUDGET,
    "budget_not_configured": BUDGET,
    "budget_rejected": BUDGET,
    "sub_budget_rejected": BUDGET,
    "cap_exceeded": BUDGET,
    "price_unknown": BUDGET,
    "kill_switch_engaged": BUDGET,
    # -31003 validation — client input cannot be processed (400/422).
    "capability_not_found": VALIDATION,
    "invalid_budget_limits": VALIDATION,
    "invalid_proxy_path": VALIDATION,
    "provider_not_found": VALIDATION,
    "provider_capability_missing": VALIDATION,
    "invalid_provider_config": VALIDATION,
    "change_set_too_broad": VALIDATION,
    "unsupported_lease_patch": VALIDATION,
    "empty_lease_patch": VALIDATION,
    "lease_not_found": VALIDATION,
    "workload_not_found": VALIDATION,
    "webhook_subscription_not_found": VALIDATION,
    "webhook_target_not_allowed": VALIDATION,
    "idempotency_mismatch": VALIDATION,
    "pre_spend_payload_rejected": VALIDATION,
    "no_serve_history": VALIDATION,
    "rate_required": VALIDATION,
    "invalid_gpu_class": VALIDATION,
    "unknown_model": VALIDATION,
    "unknown_variant": VALIDATION,
    "invalid_template": VALIDATION,
    "ttl_below_startup": VALIDATION,
    "stale_price": VALIDATION,
    "invalid_volume_file_request": VALIDATION,
    "volume_file_limit_exceeded": VALIDATION,
    "volume_file_not_found": VALIDATION,
    "volume_file_checksum_mismatch": VALIDATION,
    "invalid_gpu_selection": VALIDATION,
    "invalid_request": VALIDATION,
    "invalid_resource_id": VALIDATION,
    "resource_not_found": VALIDATION,
    "volume_grow_only": VALIDATION,
    # -31004 upstream/provider — provider/network/persistence unavailable.
    "no_providers_available": UPSTREAM,
    "no_healthy_provider": UPSTREAM,
    "provider_chain_exhausted": UPSTREAM,
    "rate_limited": UPSTREAM,
    "launch_failed": UPSTREAM,
    "warm_failed": UPSTREAM,
    "served_model_mismatch": UPSTREAM,
    "provider_error": UPSTREAM,
    "teardown_failed": UPSTREAM,
    "provider_timeout": UPSTREAM,
    "malformed_provider_response": UPSTREAM,
    "audit_unavailable": UPSTREAM,
    "audit_write_failed": UPSTREAM,
    "template_create_partial_failure": UPSTREAM,
    "registry_replace_partial_failure": UPSTREAM,
    "volume_file_provider_error": UPSTREAM,
    "volume_file_timeout": UPSTREAM,
    "volume_file_not_configured": UPSTREAM,
    "volume_file_audit_unavailable": UPSTREAM,
    "volume_file_audit_failed_after_change": UPSTREAM,
    # -31005 conflict/state — duplicate or incompatible lifecycle state.
    "capability_conflict": CONFLICT,
    "provider_conflict": CONFLICT,
    "lease_state_conflict": CONFLICT,
    "lease_expiry_limit_exceeded": CONFLICT,
    "serve_conflict": CONFLICT,
    "job_not_ready": CONFLICT,
    "job_not_cancellable": CONFLICT,
    "job_cancel_failed": UPSTREAM,
    "idempotency_conflict": CONFLICT,
    "mutation_outcome_ambiguous": CONFLICT,
    "mutation_in_progress": CONFLICT,
    "resource_name_conflict": CONFLICT,
    "volume_file_confirmation_required": CONFLICT,
    "volume_file_idempotency_conflict": CONFLICT,
    "volume_file_precondition_conflict": CONFLICT,
    "volume_file_mutation_outcome_ambiguous": CONFLICT,
    "illegal_lease_transition": CONFLICT,
    "lease_transition_error": CONFLICT,
}


def register_error_codes() -> None:
    """Populate the ``register_error_code()`` map with the class partition.

    Idempotent — repeated calls re-register the same strings to the same
    integer codes.
    """
    # Local import avoids a circular dependency at module load time:
    # ``error_adapter`` imports ``register_error_codes`` for the bootstrap.
    from pitwall.mcp.error_adapter import register_error_code

    for error_code, mcp_code in _CODE_MAP.items():
        register_error_code(error_code, mcp_code)


__all__ = [
    "AUTHZ",
    "BUDGET",
    "CONFLICT",
    "RELAY",
    "UPSTREAM",
    "VALIDATION",
    "register_error_codes",
]
