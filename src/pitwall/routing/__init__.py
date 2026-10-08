"""Routing types for the 4-stage provider selection algorithm."""

from pitwall.routing.coalescing import (
    AsyncRequestCoalescer,
    build_inference_coalescing_key,
)
from pitwall.routing.constraints import (
    DEFAULT_LB_MAX_PAYLOAD_MB,
    evaluate_hard_constraints,
)
from pitwall.routing.context import (
    AvailabilitySnapshot,
    AvailabilitySnapshotEntry,
    PlanningContext,
    freeze_provider_snapshot,
)
from pitwall.routing.cooldown import (
    DEFAULT_COOLDOWN_POLICY,
    DEFAULT_ESCALATED_COOLDOWN,
    DEFAULT_FAILURE_THRESHOLD,
    DEFAULT_INITIAL_COOLDOWN,
    MISCONFIGURED_STATUS,
    WARMING_STATUS,
    CooldownPolicy,
    CooldownState,
    CooldownStateMachine,
    OutcomeClassification,
    ProviderCooldownState,
    apply_probe_result,
    cooldown_duration_for_trip,
    is_in_cooldown,
    is_provider_in_cooldown,
    next_cooldown_state,
    record_failure,
    record_provider_failure,
    record_provider_success,
    record_success,
    state_from_provider,
    to_provider_patch,
)
from pitwall.routing.fallback import (
    DEFAULT_OPENAI_FALLBACK_BUDGET_S,
    OpenAIProxyExecutionError,
    OpenAIProxyRequest,
    OpenAIProxyResult,
    execute_openai_with_fallback,
)
from pitwall.routing.openai import (
    DEFAULT_OPENAI_MAX_ATTEMPTS,
    MAX_OPENAI_ATTEMPTS,
    OpenAIChainAttempt,
    OpenAIProviderChain,
    is_openai_compatible_provider,
    openai_base_url_for_provider,
    openai_provider_types,
    ordered_openai_providers,
    resolve_openai_chain,
    resolve_openai_provider_chain,
    resolve_openai_provider_ids,
    resolve_provider_chain,
)
from pitwall.routing.quota import (
    QuotaRecord,
    QuotaSnapshot,
    QuotaTerms,
    quota_eligible,
    quota_score_terms,
)
from pitwall.routing.saturation import SaturationDetector, SaturationSignal
from pitwall.routing.scoring import explain_score, score_provider
from pitwall.routing.types import (
    ConstraintResult,
    EliminationReason,
    Hints,
    ObservedMetrics,
    RoutingRequest,
    ScoreExplanation,
)

__all__ = [
    "ConstraintResult",
    "CooldownPolicy",
    "CooldownState",
    "CooldownStateMachine",
    "MISCONFIGURED_STATUS",
    "OutcomeClassification",
    "DEFAULT_COOLDOWN_POLICY",
    "DEFAULT_LB_MAX_PAYLOAD_MB",
    "DEFAULT_ESCALATED_COOLDOWN",
    "DEFAULT_FAILURE_THRESHOLD",
    "DEFAULT_INITIAL_COOLDOWN",
    "DEFAULT_OPENAI_FALLBACK_BUDGET_S",
    "DEFAULT_OPENAI_MAX_ATTEMPTS",
    "EliminationReason",
    "AvailabilitySnapshot",
    "AvailabilitySnapshotEntry",
    "MAX_OPENAI_ATTEMPTS",
    "Hints",
    "ObservedMetrics",
    "OpenAIChainAttempt",
    "OpenAIProviderChain",
    "OpenAIProxyExecutionError",
    "OpenAIProxyRequest",
    "OpenAIProxyResult",
    "PlanningContext",
    "ProviderCooldownState",
    "WARMING_STATUS",
    "RoutingRequest",
    "SaturationDetector",
    "SaturationSignal",
    "ScoreExplanation",
    "apply_probe_result",
    "cooldown_duration_for_trip",
    "evaluate_hard_constraints",
    "execute_openai_with_fallback",
    "freeze_provider_snapshot",
    "explain_score",
    "is_openai_compatible_provider",
    "openai_provider_types",
    "is_in_cooldown",
    "is_provider_in_cooldown",
    "next_cooldown_state",
    "openai_base_url_for_provider",
    "ordered_openai_providers",
    "record_failure",
    "record_provider_failure",
    "record_provider_success",
    "record_success",
    "resolve_openai_chain",
    "resolve_openai_provider_chain",
    "resolve_openai_provider_ids",
    "resolve_provider_chain",
    "score_provider",
    "state_from_provider",
    "to_provider_patch",
    "AsyncRequestCoalescer",
    "build_inference_coalescing_key",
    "QuotaRecord",
    "QuotaSnapshot",
    "QuotaTerms",
    "quota_eligible",
    "quota_score_terms",
]


__all__ += []

__all__ += []

__all__ += []

__all__ += []
