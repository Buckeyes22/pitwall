"""Strict transport schemas for pre-spend guardrail operator endpoints."""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import Field

from pitwall.core.models import PitwallModel
from pitwall.security.pre_spend import (
    PreSpendDecision,
    PreSpendFindingKind,
    PreSpendPolicyMode,
)


class GuardrailLimitsResponse(PitwallModel):
    max_input_bytes: int = Field(gt=0)
    max_findings: int = Field(gt=0)
    max_depth: int = Field(gt=0)
    max_items: int = Field(gt=0)
    timeout_ms: int = Field(gt=0)


class GuardrailRuleResponse(PitwallModel):
    rule_id: str
    kind: PreSpendFindingKind
    balanced_action: PreSpendDecision
    description: str


class GuardrailCountersResponse(PitwallModel):
    total: int = Field(ge=0)
    allow: int = Field(ge=0)
    redact: int = Field(ge=0)
    block: int = Field(ge=0)


class GuardrailLastDecisionResponse(PitwallModel):
    observed_at: dt.datetime
    decision: PreSpendDecision
    finding_count: int = Field(ge=0)
    rule_ids: list[str]
    inspected_bytes: int = Field(ge=0)
    elapsed_ms: int = Field(ge=0)
    limited: bool
    limit_reason: str | None


class GuardrailStatusResponse(PitwallModel):
    mode: PreSpendPolicyMode
    limits: GuardrailLimitsResponse
    rules: list[GuardrailRuleResponse]
    counters: GuardrailCountersResponse
    last_decision: GuardrailLastDecisionResponse | None


class GuardrailFindingResponse(PitwallModel):
    kind: PreSpendFindingKind
    rule: str
    path: str
    action: PreSpendDecision
    redacted_preview: str
    fingerprint_sha256: str


class GuardrailPreviewRequest(PitwallModel):
    payload: Any


class GuardrailPreviewResponse(PitwallModel):
    decision: PreSpendDecision
    blocked: bool
    findings: list[GuardrailFindingResponse]
    redacted_payload: Any
    mode: PreSpendPolicyMode
    inspected_bytes: int = Field(ge=0)
    elapsed_ms: int = Field(ge=0)
    limited: bool
    limit_reason: str | None


__all__ = [
    "GuardrailCountersResponse",
    "GuardrailFindingResponse",
    "GuardrailLastDecisionResponse",
    "GuardrailLimitsResponse",
    "GuardrailPreviewRequest",
    "GuardrailPreviewResponse",
    "GuardrailRuleResponse",
    "GuardrailStatusResponse",
]
