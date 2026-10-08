"""Read-only REST operator surface for the shared pre-spend guardrail."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from pitwall.api.guardrail_schemas import (
    GuardrailPreviewRequest,
    GuardrailPreviewResponse,
    GuardrailStatusResponse,
)
from pitwall.security.pre_spend import (
    PreSpendInspectionService,
    get_pre_spend_inspection_service,
)

router = APIRouter()


def _inspection_service(request: Request) -> PreSpendInspectionService:
    service = getattr(request.app.state, "pre_spend_inspection_service", None)
    if service is None:
        return get_pre_spend_inspection_service()
    if not isinstance(service, PreSpendInspectionService):
        raise RuntimeError("app.state.pre_spend_inspection_service has an invalid type")
    return service


@router.get("/v1/guardrails", response_model=GuardrailStatusResponse)
def guardrail_status(
    service: Annotated[PreSpendInspectionService, Depends(_inspection_service)],
) -> dict[str, Any]:
    """Return configured rules and non-sensitive aggregate process state."""
    return service.status().to_dict()


@router.post("/v1/guardrails/preview", response_model=GuardrailPreviewResponse)
def guardrail_preview(
    body: GuardrailPreviewRequest,
    service: Annotated[PreSpendInspectionService, Depends(_inspection_service)],
) -> dict[str, Any]:
    """Inspect one payload without provider, database, audit, or counter writes."""
    return service.preview(body.payload).semantic_dict()


__all__ = ["guardrail_preview", "guardrail_status", "router"]
