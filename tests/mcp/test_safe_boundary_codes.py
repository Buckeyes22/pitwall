"""The MCP safe boundary keeps Pitwall's stable error codes and nothing else."""

from __future__ import annotations

from decimal import Decimal

from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError

from pitwall.api.exceptions import CapabilityNotFound
from pitwall.cost.budget_gate import BudgetRejected, BudgetSnapshot
from pitwall.mcp.error_adapter import adapt_error
from pitwall.mcp.safe_boundary import _stable_error_payload
from pitwall.models.errors import UnknownModel, UnknownVariant

_SNAPSHOT = BudgetSnapshot(
    monthly_budget_usd=Decimal("5"),
    per_request_max_usd=Decimal("10"),
    mtd_spend_usd=Decimal("4.817201"),
    estimate_usd=Decimal("1.96"),
    budget_remaining_usd=Decimal("0.182799"),
)
_EXPECTED = {
    "error": "budget_rejected",
    "reason": "monthly_budget",
    "snapshot": {
        "monthly_budget_usd": "5",
        "per_request_max_usd": "10",
        "mtd_spend_usd": "4.817201",
        "estimate_usd": "1.96",
        "budget_remaining_usd": "0.182799",
    },
    "remedy": "raise the limit with pitwall_budget_set (a reason is required), or lower ttl_minutes or max_cost_per_hour",
}


def _wrapped(exc: Exception) -> ToolError:
    """Reproduce MCPServer's ToolError wrapping of an escaped handler exception."""
    wrapped = ToolError(str(exc))
    wrapped.__cause__ = exc
    return wrapped


def test_pitwall_api_error_keeps_its_code_and_drops_its_text() -> None:
    payload = _stable_error_payload(_wrapped(CapabilityNotFound("secret-canary.capability")))

    assert payload == {"error": "capability_not_found"}


def test_budget_rejection_keeps_its_code_and_explains_itself() -> None:
    assert _stable_error_payload(_wrapped(BudgetRejected("monthly_budget", _SNAPSHOT))) == _EXPECTED


def test_adapted_budget_rejection_keeps_its_explanation() -> None:
    adapted = adapt_error(BudgetRejected("monthly_budget", _SNAPSHOT))
    assert _stable_error_payload(_wrapped(adapted)) == _EXPECTED


def test_budget_detail_drops_unknown_and_non_decimal_fields() -> None:
    error = MCPError.from_error_data(
        adapt_error(BudgetRejected("monthly_budget", _SNAPSHOT)).error.model_copy(
            update={
                "data": {
                    "error": "budget_rejected",
                    "reason": "request text",
                    "snapshot": {"estimate_usd": "1; drop", "monthly_budget_usd": "5", "note": "x"},
                }
            }
        )
    )
    assert _stable_error_payload(_wrapped(error)) == {
        "error": "budget_rejected",
        "snapshot": {"monthly_budget_usd": "5"},
        "remedy": _EXPECTED["remedy"],
    }


def test_non_budget_errors_still_carry_only_their_code() -> None:
    assert _stable_error_payload(_wrapped(CapabilityNotFound("secret-canary.capability"))) == {
        "error": "capability_not_found"
    }


def test_an_error_code_set_on_an_instance_is_not_trusted() -> None:
    boom = RuntimeError("request text")
    boom.error_code = "request-controlled"  # type: ignore[attr-defined]  # reason: simulate an untrusted instance attribute

    assert _stable_error_payload(_wrapped(boom)) == {"error": "tool_execution_failed"}


def test_catalogue_lookup_errors_keep_their_codes() -> None:
    assert _stable_error_payload(_wrapped(UnknownModel("secret-canary/model"))) == {
        "error": "unknown_model"
    }
    assert _stable_error_payload(_wrapped(UnknownVariant("org/model", "secret-canary"))) == {
        "error": "unknown_variant"
    }


def test_audit_unavailable_carries_the_same_key_remedy_and_no_request_text() -> None:
    from pitwall.mcp.safe_boundary import SAME_KEY_RETRY_REMEDY
    from pitwall.runpod_control_plane import RunPodControlPlaneError

    # Before any create (the journal was unreachable): the id came from the request, so it
    # must not cross the boundary, and nothing claims a pod exists.
    adapted = adapt_error(
        RunPodControlPlaneError(
            "audit_unavailable",
            "durable mutation journal is unavailable REQUEST-CANARY",
            operation="pod.update",
            resource_type="pod",
            resource_id="request-supplied-id",
            retryable=True,
        )
    )
    assert _stable_error_payload(_wrapped(adapted)) == {
        "error": "audit_unavailable",
        "remedy": SAME_KEY_RETRY_REMEDY,
        "retryable": True,
    }


def test_a_pod_id_crosses_only_for_a_create() -> None:
    from pitwall.mcp.safe_boundary import SAME_KEY_RETRY_REMEDY
    from pitwall.runpod_control_plane import RunPodControlPlaneError

    # A changed pod error on another operation carries a request-supplied id: never forward it.
    adapted = adapt_error(
        RunPodControlPlaneError(
            "audit_unavailable",
            "audit store unavailable",
            operation="pod.terminate",
            resource_type="pod",
            resource_id="req-id",
            retryable=True,
            changed=True,
        )
    )
    assert _stable_error_payload(_wrapped(adapted)) == {
        "error": "audit_unavailable",
        "remedy": SAME_KEY_RETRY_REMEDY,
        "retryable": True,
    }


def test_an_inference_idempotency_mismatch_carries_its_fixed_remedy() -> None:
    from pitwall.api.exceptions import IdempotencyMismatch

    payload = _stable_error_payload(_wrapped(IdempotencyMismatch("wkl_original_canary")))
    assert payload == {
        "error": "idempotency_mismatch",
        "remedy": "this key was used for a different request; use a new idempotency_key",
        "retryable": False,
    }
    adapted = adapt_error(IdempotencyMismatch("wkl_original_canary"))
    assert _stable_error_payload(_wrapped(adapted)) == payload


def test_an_active_lease_not_serving_carries_a_fixed_stop_lease_remedy() -> None:
    from pitwall.api.exceptions import LeaseNotServing
    from pitwall.mcp.safe_boundary import LEASE_NOT_SERVING_REMEDY

    expected = {
        "error": "mutation_in_progress",
        "reason": "lease_not_serving",
        "id": "lease_runpod_0123456789ab",
        "remedy": LEASE_NOT_SERVING_REMEDY,
        "retryable": True,
    }
    error = LeaseNotServing("wkl_1", lease_id="lease_runpod_0123456789ab")
    assert _stable_error_payload(_wrapped(adapt_error(error))) == expected
    assert _stable_error_payload(_wrapped(error)) == expected
    assert "pitwall_stop_lease" in LEASE_NOT_SERVING_REMEDY
    assert LeaseNotServing.remedy == LEASE_NOT_SERVING_REMEDY  # REST and MCP say the same


def test_a_plain_in_progress_launch_carries_the_same_key_remedy() -> None:
    from pitwall.api.exceptions import LeaseLaunchInProgress

    error = LeaseLaunchInProgress("wkl_1", lease_id="lease_runpod_0123456789ab")
    assert _stable_error_payload(_wrapped(adapt_error(error))) == {
        "error": "mutation_in_progress",
        "remedy": "retry with the same idempotency_key once the first call finishes",
        "retryable": True,
    }


def test_a_not_serving_remedy_never_carries_a_malformed_lease_id() -> None:
    data = {"error": "mutation_in_progress", "reason": "lease_not_serving", "id": "x y REQUEST"}
    from pitwall.api.exceptions import LeaseLaunchInProgress

    error = MCPError.from_error_data(
        adapt_error(LeaseLaunchInProgress("wkl_1")).error.model_copy(update={"data": data})
    )
    payload = _stable_error_payload(_wrapped(error))
    assert "id" not in payload and payload["reason"] == "lease_not_serving"
