"""Alibaba Cloud Model Studio provider adapter (OpenAI-compatible, always streaming)."""

from __future__ import annotations

import datetime as dt
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from pitwall.core.enums import CostMode, ProviderType
from pitwall.core.models import Capability
from pitwall.core.models import Provider as ProviderRecord
from pitwall.cost.estimator import (
    PerTokenPricing,
    TaggedPricingModel,
    ZeroOrEnergyPricing,
    output_token_ceiling,
    parse_pricing_model,
)
from pitwall.providers.errors import ProviderQuotaExhausted, QuotaReason
from pitwall.providers.interface import (
    AvailabilityItem,
    AvailabilityKind,
    AvailabilityRequest,
    AvailabilityResult,
    InferenceRequest,
    InferenceResult,
    ProviderCapability,
    ProviderDeclaration,
    optional_config_string,
    quota_window,
    resolve_adapter_credentials,
)
from pitwall.providers.model_studio import catalog, openapi

_MAX_ERROR_BODY_CHARS = 500
_EXCLUSIVE = "exclusiveWith"
_MAX_RETRY_AFTER = dt.timedelta(hours=24)


class ModelStudioCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    api_key: SecretStr = Field(min_length=1)
    timeout_s: float = Field(default=600.0, gt=0)


class ModelStudioProviderError(RuntimeError):
    def __init__(self, status_code: int, classification: str, body: str) -> None:
        self.status_code = status_code
        self.classification = classification
        super().__init__(
            f"Model Studio request failed with HTTP {status_code} ({classification}): {body[:_MAX_ERROR_BODY_CHARS]}"
        )


class ModelStudioQuotaExhausted(ModelStudioProviderError, ProviderQuotaExhausted):
    """Typed quota or rate-limit signal that routing turns into a (provider, model) lockout."""

    def __init__(
        self,
        status_code: int,
        body: str,
        *,
        reason: QuotaReason,
        reset_at: dt.datetime | None,
    ) -> None:
        super().__init__(status_code, reason, body)
        self.reason = reason
        self.reset_at = reset_at


class _StreamErrorChunk(Exception):
    """An SSE ``data:`` frame carried an error object instead of a completion delta."""

    def __init__(self, body: str) -> None:
        super().__init__(body)
        self.body = body


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelStudioInferenceResult(InferenceResult):
    content: str | None
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    finish_reason: str | None
    raw: Mapping[str, Any] = field(default_factory=dict)


def build_chat_body(
    payload: Mapping[str, Any], settings: Mapping[str, Any], record: Mapping[str, Any]
) -> dict[str, Any]:
    body = dict(payload)
    model = str(settings["model"])
    requested = body.get("model")
    if requested not in (None, model):
        raise catalog.ModelStudioConfigError(
            "model_mismatch", f"payload model must be {model!r} for this provider"
        )
    body["model"] = model
    effort = record.get("effort")
    if "reasoning_effort" in body:
        if not effort:
            raise catalog.ModelStudioConfigError(
                "invalid_thinking_parameters", f"{model} has no reasoning_effort control"
            )
        if body["reasoning_effort"] not in effort["values"]:
            raise catalog.ModelStudioConfigError(
                "invalid_thinking_parameters",
                f"reasoning_effort must be one of {', '.join(effort['values'])} for {model}",
            )
        for other in effort.get(_EXCLUSIVE, []):
            if other in body:
                raise catalog.ModelStudioConfigError(
                    "invalid_thinking_parameters",
                    f"reasoning_effort cannot be combined with {other} on {model}",
                )
    ceiling = int(output_token_ceiling(payload, record.get("maxOutputTokens")))
    for key in ("max_tokens", "max_output_tokens", "max_completion_tokens", "max_new_tokens"):
        body.pop(key, None)
    body["max_tokens" if record.get("maxTokensIncludesReasoning") else "max_completion_tokens"] = (
        ceiling
    )
    body["stream"] = True
    options = body.get("stream_options")
    body["stream_options"] = {
        **(options if isinstance(options, Mapping) else {}),
        "include_usage": True,
    }
    return body


def _model_studio_skip_reason(provider: Any) -> str | None:
    try:
        settings = catalog.provider_settings(provider.config)
        catalog.require_automation(settings, os.environ)
    except catalog.ModelStudioConfigError as exc:
        return str(exc)
    return None


def _model_studio_rewrite_body(content: bytes, provider: Any) -> bytes:
    return catalog.rewrite_proxy_body(content, catalog.provider_settings(provider.config))


def _validate_model_studio_url(
    provider_type: str | None,
    endpoint_id: str | None,
    config: Mapping[str, Any],
) -> None:
    try:
        settings = catalog.provider_settings(config)
    except catalog.ModelStudioConfigError as exc:
        raise ValueError(str(exc)) from exc
    expected = catalog.base_url(settings)
    if optional_config_string(config, "openai_base_url") not in (None, expected):
        raise ValueError(
            f"config.openai_base_url must be {expected!r} for provider_type 'model_studio'"
        )


def _model_studio_seed_config(
    spec: Mapping[str, Any],
    config: dict[str, Any],
    provider_type: str,
    endpoint_id: str | None,
) -> dict[str, Any]:
    section = spec.get("model_studio", config.get("model_studio", {}))
    if section is None:
        section = {}
    if not isinstance(section, Mapping):
        raise ValueError("provider.model_studio must be an object")
    try:
        settings = catalog.provider_settings({"model_studio": dict(section)})
        if "cost" not in spec and "cost" not in (spec.get("config") or {}):
            config["cost"] = catalog.pricing_config(settings)
    except catalog.ModelStudioConfigError as exc:
        raise ValueError(str(exc)) from exc
    config["model_studio"] = settings
    config["openai_base_url"] = catalog.base_url(settings)
    config["supports_streaming"] = True
    return config


async def _model_studio_quota_tick(
    repo: Any, prov: Mapping[str, Any], now: dt.datetime, existing: Mapping[Any, Any]
) -> None:
    settings = catalog.provider_settings(prov.get("config") or {})
    plan = str(settings["plan"])
    if not catalog.is_token_plan(plan):
        spend = await openapi.get_billing_month_to_date(
            os.environ, model=str(settings["model"]), now=now
        )
        start, reset = quota_window("recurring-monthly", now)
        await repo.refresh_window(
            prov["id"],
            "",
            free_type="pay-as-you-go",
            window_start=start,
            reset_at=reset,
            budget_units=None,
            used_units=spend,
            tos_verdict="ok",
            evidence_patch={
                "source": "GetBillingOverview" if spend is not None else "not-configured",
                "currency": "USD",
                "model": settings["model"],
            },
        )
        return
    tier = catalog.load_catalog()["plans"][plan]["tiers"][settings["tier"]]
    accepted = catalog.automation_accepted(settings, os.environ)
    stats = await openapi.get_subscription_stats(os.environ, now=now)
    if stats is not None:
        start, reset = stats.window_start, stats.reset_at
        budget: Decimal | None = stats.total_credits
        used: Decimal | None = stats.total_credits - stats.remaining_credits
        source = "openapi-stats"
    else:
        start, reset = (
            catalog.credits_window(settings["renews_on"], now)
            if settings.get("renews_on")
            else (None, None)
        )
        budget, used, source = Decimal(tier["credits"]), None, "configured-tier"
    await repo.refresh_window(
        prov["id"],
        "",
        free_type="subscription-credits",
        window_start=start,
        reset_at=reset,
        budget_units=budget,
        used_units=used,
        tos_verdict="caution" if accepted else "avoid",
        evidence_patch={
            "source": source,
            "plan": plan,
            "tier": settings["tier"],
            "automation": "accepted" if accepted else "not-accepted",
        },
    )


MODEL_STUDIO_DECLARATION = ProviderDeclaration(
    provider_types=frozenset({ProviderType.MODEL_STUDIO.value}),
    dedicated_provider_type=True,
    lockout_model_paths=(("model_studio", "model"),),
    requires_gpu_class=False,
    validate_url=_validate_model_studio_url,
    seed_config=_model_studio_seed_config,
    proxy_skip_reason=_model_studio_skip_reason,
    proxy_rewrite_body=_model_studio_rewrite_body,
    quota_tick=_model_studio_quota_tick,
)


class ModelStudioProvider:
    id = "model_studio"
    name = "Alibaba Cloud Model Studio"
    credential_schema = ModelStudioCredentials
    declaration = MODEL_STUDIO_DECLARATION
    capabilities = frozenset({ProviderCapability.SYNC_INFERENCE, ProviderCapability.AVAILABILITY})

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        environ: Mapping[str, str] | None = None,
        now: Callable[[], dt.datetime] | None = None,
    ) -> None:
        self._transport = transport
        self._environ = environ
        self._now = now or (lambda: dt.datetime.now(dt.UTC))

    def _env(self) -> Mapping[str, str]:
        return os.environ if self._environ is None else self._environ

    def pricing_model(
        self,
        capability: Capability,
        provider_record: ProviderRecord,
    ) -> TaggedPricingModel:
        mode = CostMode(capability.cost_mode)
        pricing = parse_pricing_model(provider_record, cost_mode=mode)
        if not isinstance(pricing, (ZeroOrEnergyPricing, PerTokenPricing)):
            raise ValueError("ModelStudioProvider requires zero or per_token pricing")
        return pricing

    async def infer(self, request: InferenceRequest) -> ModelStudioInferenceResult:
        credentials, provider_record, payload = (
            request.credentials,
            request.provider_record,
            request.payload,
        )
        settings = catalog.provider_settings(provider_record.config)
        catalog.require_automation(settings, self._env())
        resolved = resolve_adapter_credentials(
            credentials, ModelStudioCredentials, adapter_id=self.id
        )
        key = resolved.api_key.get_secret_value()
        catalog.check_key(str(settings["plan"]), key)
        record = catalog.check_model(str(settings["plan"]), str(settings["model"]))
        body = build_chat_body(payload, settings, record)
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        async with (
            httpx.AsyncClient(
                base_url=catalog.base_url(settings),
                timeout=resolved.timeout_s,
                transport=self._transport,
            ) as client,
            client.stream("POST", "chat/completions", headers=headers, json=body) as response,
        ):
            if response.status_code >= 400:
                text = (await response.aread()).decode("utf-8", errors="replace")
                raise self._error(response.status_code, text, settings, response.headers)
            try:
                return await _collect_stream(
                    response, provider_id=provider_record.id, fallback_model=str(settings["model"])
                )
            except _StreamErrorChunk as chunk:
                raise self._stream_error(chunk.body, settings) from None

    def _stream_error(self, body: str, settings: Mapping[str, Any]) -> Exception:
        """Classify an error frame from a 200 stream as the HTTP error it stands in for."""
        for status in (429, 400, 503):
            if catalog.classify_error(status, body) is not None:
                return self._error(status, body, settings)
        return ModelStudioProviderError(200, "stream_error", body)

    def _error(
        self,
        status: int,
        body: str,
        settings: Mapping[str, Any],
        headers: Mapping[str, str] | None = None,
    ) -> Exception:
        classified = catalog.classify_error(status, body)
        now = self._now()
        if classified is None:
            return ModelStudioProviderError(status, "unclassified", body)
        classification, cooldown = classified
        retry_after = _retry_after(headers, now)

        def wait(default_s: int) -> dt.datetime:
            return now + (retry_after or dt.timedelta(seconds=cooldown or default_s))

        if classification == "rate_limit":
            return ModelStudioQuotaExhausted(
                status, body, reason="rate_limit_exceeded", reset_at=wait(60)
            )
        if classification == "credits_exhausted":
            return ModelStudioQuotaExhausted(
                status, body, reason="quota_exhausted", reset_at=catalog.next_renewal(settings, now)
            )
        if classification == "billing_state":
            return ModelStudioQuotaExhausted(
                status, body, reason="billing_state", reset_at=wait(3600)
            )
        if classification == "unavailable":
            return ModelStudioQuotaExhausted(
                status, body, reason="model_capacity", reset_at=wait(60)
            )
        return ModelStudioProviderError(status, classification, body)

    async def availability(self, request: AvailabilityRequest) -> AvailabilityResult:
        settings = catalog.provider_settings(request.provider_record.config)
        catalog.require_automation(settings, self._env())
        resolved = resolve_adapter_credentials(
            request.credentials, ModelStudioCredentials, adapter_id=self.id
        )
        key = resolved.api_key.get_secret_value()
        catalog.check_key(str(settings["plan"]), key)
        # The OpenAI-compatible model list, not the native /api/v1/models: the Token Plan host
        # answers the native path with 404 (live check, 2026-09-26).
        async with httpx.AsyncClient(
            base_url=catalog.base_url(settings),
            timeout=resolved.timeout_s,
            transport=self._transport,
        ) as client:
            response = await client.get("models", headers={"Authorization": f"Bearer {key}"})
        if response.status_code >= 400:
            raise self._error(response.status_code, response.text, settings, response.headers)
        models = _model_list(response)
        items = tuple(
            AvailabilityItem(
                provider_id=request.provider_record.id,
                resource_id=str(item["id"]),
                kind=AvailabilityKind.MODEL,
                available=True,
                pricing={},
                attributes={},
            )
            for item in models
            if isinstance(item, Mapping) and item.get("id") == settings["model"]
        )
        observed = request.context.now or self._now()
        return AvailabilityResult(
            provider_id=request.provider_record.id,
            observed_at=observed.astimezone(dt.UTC),
            source_contract="model-studio-compatible-models-2026-09-26",
            items=items[: request.limit],
        )


async def _collect_stream(
    response: httpx.Response, *, provider_id: str, fallback_model: str
) -> ModelStudioInferenceResult:
    content: list[str] = []
    reasoning: list[str] = []
    tool_calls: dict[int, dict[str, Any]] = {}
    finish_reason: str | None = None
    model = fallback_model
    usage: Mapping[str, Any] | None = None
    frames = 0
    async for line in response.aiter_lines():
        if not line.startswith("data:"):
            continue
        frames += 1
        data = line[5:].strip()
        if data == "[DONE]":
            break
        chunk = _parse_frame(data, response.status_code)
        if chunk.get("error") is not None:
            raise _StreamErrorChunk(data)
        model = str(chunk.get("model") or model)
        if isinstance(chunk.get("usage"), Mapping):
            usage = chunk["usage"]
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            if delta.get("content"):
                content.append(str(delta["content"]))
            if delta.get("reasoning_content"):
                reasoning.append(str(delta["reasoning_content"]))
            for call in delta.get("tool_calls") or []:
                slot = tool_calls.setdefault(
                    int(call.get("index", 0)),
                    {"id": None, "type": "function", "function": {"name": "", "arguments": ""}},
                )
                slot["id"] = call.get("id") or slot["id"]
                function = call.get("function") or {}
                slot["function"]["name"] += function.get("name") or ""
                slot["function"]["arguments"] += function.get("arguments") or ""
            finish_reason = choice.get("finish_reason") or finish_reason
    if frames == 0:
        raise ModelStudioProviderError(
            response.status_code,
            "non_sse_response",
            "the 200 response carried no server-sent-event data frames",
        )
    text = "".join(content) or None
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if reasoning:
        message["reasoning_content"] = "".join(reasoning)
    if tool_calls:
        message["tool_calls"] = [tool_calls[index] for index in sorted(tool_calls)]
    raw: dict[str, Any] = {
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
    }
    prompt = completion = total = None
    if usage is not None:
        raw["usage"] = dict(usage)
        prompt = _count(usage.get("prompt_tokens"))
        completion = _count(usage.get("completion_tokens"))
        total = _count(usage.get("total_tokens"))
        if total is None and prompt is not None and completion is not None:
            total = prompt + completion
    output = {
        "content": text,
        "model": model,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "finish_reason": finish_reason,
    }
    return ModelStudioInferenceResult(
        provider_id=provider_id,
        output=output,
        content=text,
        model=model,
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        finish_reason=finish_reason,
        raw=raw,
    )


def _parse_frame(data: str, status_code: int) -> dict[str, Any]:
    try:
        chunk = json.loads(data)
    except ValueError:
        chunk = None
    if not isinstance(chunk, dict):
        raise ModelStudioProviderError(status_code, "malformed_stream", data)
    return chunk


def _model_list(response: httpx.Response) -> list[object]:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(payload, Mapping) or not (data is None or isinstance(data, list)):
        raise ModelStudioProviderError(response.status_code, "malformed_response", response.text)
    return list(data or [])


def _retry_after(headers: Mapping[str, str] | None, now: dt.datetime) -> dt.timedelta | None:
    """Read ``Retry-After`` as delta seconds or an HTTP date, capped at 24 hours."""
    value = ((headers.get("retry-after") if headers is not None else None) or "").strip()
    if not value:
        return None
    if value.isdigit():
        return min(dt.timedelta(seconds=int(value)), _MAX_RETRY_AFTER)
    try:
        when = parsedate_to_datetime(value)
    except TypeError, ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.UTC)
    delta = when - now
    return min(delta, _MAX_RETRY_AFTER) if delta > dt.timedelta(0) else None


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
