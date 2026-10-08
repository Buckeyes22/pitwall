"""Together AI provider plugin adapter."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any
from urllib.parse import urlsplit

import httpx
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, SecretStr, field_validator

from pitwall.core.enums import CostMode
from pitwall.core.models import Capability
from pitwall.core.models import Provider as ProviderRecord
from pitwall.cost.estimator import PerTokenPricing, TaggedPricingModel, parse_pricing_model
from pitwall.providers.interface import (
    AvailabilityItem,
    AvailabilityKind,
    AvailabilityRequest,
    AvailabilityResult,
    InferenceRequest,
    InferenceResult,
    ProviderCapability,
    ProviderDeclaration,
    resolve_adapter_credentials,
)

SafeTogetherUrl = Annotated[str, AfterValidator(lambda value: _safe_together_url(value))]

_DEFAULT_BASE_URL = "https://api.together.ai/v1"
_CHAT_COMPLETIONS_PATH = "chat/completions"
_MAX_ERROR_BODY_CHARS = 500


class TogetherCredentials(BaseModel):
    """Credentials required for Together API operations."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    api_key: SecretStr = Field(min_length=1)
    base_url: SafeTogetherUrl = _DEFAULT_BASE_URL
    timeout_s: float = Field(default=330.0, gt=0)

    @field_validator("api_key")
    @classmethod
    def _validate_api_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("api_key must be non-empty")
        return value


class TogetherProviderError(RuntimeError):
    """Safe-to-log Together API failure."""

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        super().__init__(
            f"Together API request failed with HTTP {status_code}: {_compact_error_body(body)}"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class TogetherInferenceResult(InferenceResult):
    """Normalized result with the existing Together convenience fields."""

    content: str | None
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    finish_reason: str | None
    raw: Mapping[str, Any] = field(default_factory=dict)


#: No provider types of its own: rows ride on the shared pod/inference surfaces and take the
#: default OpenAI-proxy, lockout, seed, and reconcile behaviour.
TOGETHER_DECLARATION = ProviderDeclaration()


class TogetherProvider:
    """Provider plugin backed by Together's OpenAI-compatible inference API."""

    id = "together"
    name = "Together AI"
    credential_schema = TogetherCredentials
    declaration = TOGETHER_DECLARATION
    capabilities = frozenset({ProviderCapability.SYNC_INFERENCE, ProviderCapability.AVAILABILITY})

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    def pricing_model(
        self,
        capability: Capability,
        provider_record: ProviderRecord,
    ) -> TaggedPricingModel:
        if CostMode(capability.cost_mode) != CostMode.PER_TOKEN:
            raise ValueError("TogetherProvider requires per_token capability cost_mode")
        pricing = parse_pricing_model(provider_record, cost_mode=CostMode.PER_TOKEN)
        if not isinstance(pricing, PerTokenPricing):
            raise ValueError("TogetherProvider requires per_token pricing")
        return pricing

    async def availability(self, request: AvailabilityRequest) -> AvailabilityResult:
        """Return Together's bounded public model catalogue and published pricing."""

        resolved = resolve_adapter_credentials(
            request.credentials,
            TogetherCredentials,
            adapter_id=self.id,
        )
        headers = {"Authorization": f"Bearer {resolved.api_key.get_secret_value()}"}
        async with httpx.AsyncClient(
            base_url=resolved.base_url,
            timeout=resolved.timeout_s,
            transport=self._transport,
        ) as client:
            response = await client.get("models", headers=headers)
        if response.status_code >= 400:
            raise TogetherProviderError(response.status_code, response.text)
        raw = _json_value_response(response)
        if not isinstance(raw, list) or not all(isinstance(item, Mapping) for item in raw):
            raise TogetherProviderError(
                response.status_code,
                "response body was not a JSON model array",
            )
        items = tuple(
            sorted(
                (_availability_item(request.provider_record.id, item) for item in raw),
                key=lambda item: item.resource_id,
            )[: request.limit]
        )
        observed_at = request.context.now or dt.datetime.now(dt.UTC)
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("availability context now must be timezone-aware")
        return AvailabilityResult(
            provider_id=request.provider_record.id,
            observed_at=observed_at.astimezone(dt.UTC),
            source_contract="together-v1-models-2026-09-01",
            items=items,
        )

    async def infer(self, request: InferenceRequest) -> TogetherInferenceResult:
        """Run one Together chat completion with header-only authentication."""

        credentials, provider_record, payload = (
            request.credentials,
            request.provider_record,
            request.payload,
        )

        resolved_credentials = resolve_adapter_credentials(
            credentials,
            TogetherCredentials,
            adapter_id=self.id,
        )
        body = _payload_with_model(provider_record, payload)
        headers = {
            "Authorization": f"Bearer {resolved_credentials.api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(
            base_url=resolved_credentials.base_url,
            timeout=resolved_credentials.timeout_s,
            transport=self._transport,
        ) as client:
            response = await client.post(_CHAT_COMPLETIONS_PATH, headers=headers, json=body)

        if response.status_code >= 400:
            raise TogetherProviderError(response.status_code, response.text)

        data = _json_response(response)
        return _inference_result(
            data,
            fallback_model=str(body["model"]),
            provider_id=provider_record.id,
        )


def _payload_with_model(
    provider_record: ProviderRecord,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    body = dict(payload)
    priced_model = _provider_model(provider_record)
    if priced_model is None:
        raise ValueError("Together inference requires a non-empty provider-priced model")
    requested_model = _optional_non_empty_string(body.get("model"))
    if requested_model is not None and requested_model != priced_model:
        raise ValueError(
            f"Together payload model must match the provider-priced model {priced_model!r}"
        )
    body["model"] = priced_model
    return body


def _provider_model(provider_record: ProviderRecord) -> str | None:
    config = provider_record.config
    if not isinstance(config, Mapping):
        return None
    for key in ("model", "model_id", "together_model"):
        model = _optional_non_empty_string(config.get(key))
        if model is not None:
            return model
    return None


def _json_value_response(response: httpx.Response) -> object:
    try:
        raw: object = json.loads(response.text, parse_float=Decimal)
    except json.JSONDecodeError as exc:
        raise TogetherProviderError(response.status_code, "response body was not JSON") from exc
    return raw


def _json_response(response: httpx.Response) -> Mapping[str, Any]:
    raw = _json_value_response(response)
    if not isinstance(raw, Mapping):
        raise TogetherProviderError(response.status_code, "response body was not a JSON object")
    return raw


def _availability_item(provider_id: str, raw: Mapping[str, Any]) -> AvailabilityItem:
    model_id = _optional_non_empty_string(raw.get("id"))
    if model_id is None:
        raise TogetherProviderError(200, "model item omitted id")
    pricing_value = raw.get("pricing")
    pricing_body = pricing_value if isinstance(pricing_value, Mapping) else {}
    pricing: dict[str, Decimal] = {}
    for source_key, target_key in (
        ("input", "usd_per_million_input_tokens"),
        ("output", "usd_per_million_output_tokens"),
        ("cached_input", "usd_per_million_cached_input_tokens"),
        ("hourly", "usd_per_hour"),
    ):
        value = pricing_body.get(source_key)
        if value is not None:
            pricing[target_key] = _non_negative_decimal(value, f"pricing.{source_key}")
    return AvailabilityItem(
        provider_id=provider_id,
        resource_id=model_id,
        kind=AvailabilityKind.MODEL,
        available=True,
        pricing=pricing,
        attributes={
            key: value
            for key, value in {
                "type": raw.get("type"),
                "display_name": raw.get("display_name"),
                "organization": raw.get("organization"),
                "context_length": raw.get("context_length"),
            }.items()
            if value is not None
        },
    )


def _non_negative_decimal(value: object, name: str) -> Decimal:
    if isinstance(value, bool):
        raise TogetherProviderError(200, f"{name} was not a decimal value")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise TogetherProviderError(200, f"{name} was not a decimal value") from exc
    if not result.is_finite() or result < 0:
        raise TogetherProviderError(200, f"{name} was not a non-negative finite decimal")
    return result


def _inference_result(
    data: Mapping[str, Any],
    *,
    fallback_model: str,
    provider_id: str,
) -> TogetherInferenceResult:
    choice = _first_choice(data.get("choices"))
    message = choice.get("message")
    message_body = message if isinstance(message, Mapping) else {}
    usage = data.get("usage")
    usage_body = usage if isinstance(usage, Mapping) else {}
    prompt_tokens = _optional_token_count(usage_body.get("prompt_tokens"))
    completion_tokens = _optional_token_count(usage_body.get("completion_tokens"))
    total_tokens = _optional_token_count(usage_body.get("total_tokens"))
    if total_tokens is None and prompt_tokens is not None and completion_tokens is not None:
        total_tokens = prompt_tokens + completion_tokens

    content = _optional_non_empty_string(message_body.get("content")) or _optional_non_empty_string(
        choice.get("text")
    )
    model = _optional_non_empty_string(data.get("model")) or fallback_model
    finish_reason = _optional_non_empty_string(choice.get("finish_reason"))
    output = {
        "content": content,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "finish_reason": finish_reason,
    }
    return TogetherInferenceResult(
        provider_id=provider_id,
        output=output,
        content=content,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        finish_reason=finish_reason,
        raw=dict(data),
    )


def _first_choice(choices: object) -> Mapping[str, Any]:
    if isinstance(choices, list) and choices:
        choice = choices[0]
        if isinstance(choice, Mapping):
            return choice
    return {}


def _optional_token_count(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, Decimal):
        try:
            integral = value.to_integral_value()
            return int(integral) if integral == value and integral >= 0 else None
        except ArithmeticError, ValueError:
            return None
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdecimal():
            try:
                return int(stripped)
            except ValueError:
                return None
    return None


def _optional_non_empty_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _safe_together_url(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("url must be non-empty")
    parsed = urlsplit(stripped)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("url must be an absolute http(s) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("url must not include user info")
    if parsed.query or parsed.fragment:
        raise ValueError("url must not include query strings or fragments")
    return stripped.rstrip("/")


def _compact_error_body(body: str) -> str:
    normalized = " ".join(body.split())
    if not normalized:
        return "<empty body>"
    if len(normalized) <= _MAX_ERROR_BODY_CHARS:
        return normalized
    return f"{normalized[:_MAX_ERROR_BODY_CHARS]}..."


__all__ = [
    "SafeTogetherUrl",
    "TogetherCredentials",
    "TogetherInferenceResult",
    "TogetherProvider",
    "TogetherProviderError",
]
