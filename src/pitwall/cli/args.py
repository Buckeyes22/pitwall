"""Argument types and the pre-spend guard shared by CLI command modules."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from typing import Any


def positive_minutes(value: str) -> int:
    minutes = int(value)
    if minutes < 1:
        raise argparse.ArgumentTypeError("ttl minutes must be >= 1")
    return minutes


def positive_context_length(value: str) -> int:
    context_length = int(value)
    if context_length < 1:
        raise argparse.ArgumentTypeError("context length must be >= 1")
    return context_length


def guard_cli_pre_spend(payload: Mapping[str, object], *, preview: bool = False) -> None:
    """Apply the shared guard before a CLI path opens a pool or provider client."""
    from pitwall.api.exceptions import PreSpendPayloadRejected
    from pitwall.security.pre_spend import PreSpendDecision, get_pre_spend_inspection_service

    service = get_pre_spend_inspection_service()
    result = service.preview(payload) if preview else service.inspect(payload)
    if result.decision != PreSpendDecision.ALLOW:
        raise PreSpendPayloadRejected(
            decision=result.decision.value,
            findings=[finding.to_dict() for finding in result.findings],
        )


def enum_values(enum_type: type[Any]) -> list[str]:
    return [item.value for item in enum_type]


def normalize_model_id(model: str) -> str:
    """Accept the dossier filename spelling for one model-id separator."""
    if "/" not in model:
        return model.replace("--", "/", 1)
    return model
