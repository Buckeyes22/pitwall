"""OpenAI-compatible gateway adapter for free and cheap pools (research §9.1)."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationInfo,
    field_validator,
)

from pitwall.core.enums import CostMode, ProviderType
from pitwall.core.models import Capability
from pitwall.core.models import Provider as ProviderRecord
from pitwall.cost.estimator import (
    PerTokenPricing,
    TaggedPricingModel,
    ZeroOrEnergyPricing,
    parse_pricing_model,
)
from pitwall.providers.errors import ProviderQuotaExhausted, QuotaReason
from pitwall.providers.interface import (
    AvailabilityItem,
    AvailabilityKind,
    AvailabilityRequest,
    AvailabilityResult,
    CredentialInput,
    CredentialReference,
    InferenceRequest,
    InferenceResult,
    ProviderCapability,
    ProviderDeclaration,
    quota_window,
    resolve_adapter_credentials,
)
from pitwall.resolver.provider_urls import is_loopback_base_url, validate_openai_base_url

CATALOG_SOURCE_CONTRACT = "gateway-catalog-2026-09-10"
_CHAT_COMPLETIONS_PATH = "chat/completions"
_MAX_ERROR_BODY_CHARS = 500
FORK_TOKEN_ENV = "PITWALL_GATEWAY_TOKEN"
COMPRESSION_HEADER = "x-pitwall-compression"
ROUTE_HEADER = "x-pitwall-route"

# Duration semantics ported from OmniRoute accountFallback.ts (retry-after regexes) and
# constants.ts RateLimitReason; permanent signals are terminal, never auto-cleared.
_RETRY_IN_S = re.compile(r"retry (?:in|after)\s+([\d.]+)\s*s", re.I)
_RESET_AT_ISO = re.compile(
    r"(?:reset(?:s)? at|try again at|available at)\s+(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?Z?)",
    re.I,
)
_DAILY = re.compile(r"\b(daily|per day|today)\b", re.I)
_MONTHLY = re.compile(r"\b(monthly|this month|per month)\b", re.I)
_PERMANENT = (
    "account has been deactivated",
    "account suspended",
    "permanently banned",
    "access revoked",
)


class GatewayCredentials(BaseModel):
    """Bearer key is optional: keyless pools carry no credential at all."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    api_key: SecretStr | None = None
    # Explicit opt-in for a pool on a private address; it relaxes only the base_url guard.
    local: bool = False
    base_url: str = "http://127.0.0.1:20130/v1"
    timeout_s: float = Field(default=120.0, gt=0)

    @field_validator("base_url")
    @classmethod
    def _safe_base_url(cls, value: str, info: ValidationInfo) -> str:
        return validate_openai_base_url(value, allow_local=bool(info.data.get("local")))


class GatewayProviderError(RuntimeError):
    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        super().__init__(
            f"gateway request failed with HTTP {status_code}: {_compact_error_body(body)}"
        )


class QuotaExhausted(GatewayProviderError, ProviderQuotaExhausted):
    """Typed 429 signal consumed by routing/lockout.py."""

    def __init__(
        self,
        status_code: int,
        body: str,
        *,
        reason: QuotaReason,
        reset_at: dt.datetime | None,
    ) -> None:
        super().__init__(status_code, body)
        self.reason = reason
        self.reset_at = reset_at


@dataclass(frozen=True, slots=True, kw_only=True)
class GatewayInferenceResult(InferenceResult):
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    raw: Mapping[str, Any] = field(default_factory=dict)


_MAX_DELTA = dt.timedelta(hours=24)
_EPOCH_MS_FLOOR = 1_000_000_000_000
_EPOCH_S_FLOOR = 1_000_000_000


def _as_utc(value: dt.datetime) -> dt.datetime:
    """Read a naive timestamp as UTC, never as the host's local time."""
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC)


def _parse_reset_header(value: str, *, now: dt.datetime) -> tuple[QuotaReason, dt.datetime] | None:
    """Read a reset header as delta seconds, epoch seconds or milliseconds, or ISO time.

    Deltas are capped at 24 hours, naive timestamps are taken as UTC, and anything else
    returns ``None`` so the caller falls back to the body signals and default backoff.
    """
    if value.isdigit():
        number = int(value)
        if number >= _EPOCH_MS_FLOOR:
            return "quota_exhausted", dt.datetime.fromtimestamp(number / 1000, tz=dt.UTC)
        if number >= _EPOCH_S_FLOOR:
            return "quota_exhausted", dt.datetime.fromtimestamp(number, tz=dt.UTC)
        return "rate_limit_exceeded", now + min(dt.timedelta(seconds=number), _MAX_DELTA)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return "quota_exhausted", _as_utc(parsed)


def classify_429(
    status: int,
    body: str,
    headers: Mapping[str, str],
    *,
    now: dt.datetime,
) -> tuple[QuotaReason, dt.datetime | None]:
    text = body.lower()
    if any(signal in text for signal in _PERMANENT):
        return "permanent_ban", None
    reset_header = headers.get("x-ratelimit-reset") or headers.get("retry-after")
    if reset_header:
        parsed = _parse_reset_header(reset_header.strip(), now=now)
        if parsed is not None:
            return parsed
    if match := _RETRY_IN_S.search(body):
        return "rate_limit_exceeded", now + dt.timedelta(seconds=float(match.group(1)))
    if match := _RESET_AT_ISO.search(body):
        return "quota_exhausted", _as_utc(dt.datetime.fromisoformat(match.group(1)))
    if _DAILY.search(body):
        return (
            "quota_exhausted",
            (now + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0),
        )
    if _MONTHLY.search(body):
        first_next = (now.replace(day=1) + dt.timedelta(days=32)).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        return "quota_exhausted", first_next
    if status in (503, 529):
        return "model_capacity", None
    return "rate_limit_exceeded", None


def _gateway_proxy_headers(provider: Any, outbound: dict[str, str]) -> dict[str, str] | None:
    fork = fork_upstream_headers(provider)
    if not fork:
        return None
    # The loopback fork authenticates with its own token and resolves the upstream from the
    # route; pool keys stay with the fork.
    outbound.update({key.lower(): value for key, value in fork.items()})
    return outbound


def _gateway_seed_config(
    spec: Mapping[str, Any],
    config: dict[str, Any],
    provider_type: str,
    endpoint_id: str | None,
) -> dict[str, Any]:
    streaming = spec.get(
        "supports_streaming",
        spec.get("supportsStreaming", config.get("supports_streaming", False)),
    )
    if not isinstance(streaming, bool):
        raise ValueError("provider.supports_streaming must be a boolean")
    config["supports_streaming"] = streaming
    raw = spec.get("gateway", config.get("gateway", {}))
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ValueError("provider.gateway must be an object")
    gateway = dict(raw)
    for key in ("base_url", "model_id"):
        value = gateway.get(key)
        if value is None or not str(value).strip():
            raise ValueError(f"openai_gateway providers require gateway.{key}")
    catalog = gateway.get("catalog", {})
    if catalog is None:
        catalog = {}
    if not isinstance(catalog, Mapping):
        raise ValueError("provider.gateway.catalog must be an object")
    gateway["catalog"] = dict(catalog)
    config["gateway"] = gateway
    config["openai_base_url"] = gateway["base_url"]
    return config


async def _gateway_quota_tick(
    repo: Any, prov: Mapping[str, Any], now: dt.datetime, existing: Mapping[Any, Any]
) -> None:
    from pitwall.routing.quota import QuotaRecord  # local: routing imports the providers package

    catalog = ((prov.get("config") or {}).get("gateway") or {}).get("catalog") or {}
    free_type = str(catalog.get("free_type", "recurring-uncapped"))
    pool_key = str(catalog.get("pool_key") or "")
    record = existing.get((prov["id"], pool_key))
    if record is None:
        window_start, reset_at = quota_window(free_type, now)
        budget = catalog.get("monthly_tokens") or catalog.get("credit_tokens") or None
        record = QuotaRecord(
            provider_id=prov["id"],
            pool_key=pool_key,
            free_type=free_type,
            window_start=window_start,
            reset_at=reset_at,
            budget_units=Decimal(str(budget)) if budget else None,
            used_units=Decimal(0),
            tos_verdict=str(catalog.get("tos", "unknown")),
            evidence={},
        )
        await repo.upsert(record)
    elif record.reset_at is not None and record.reset_at <= now:
        # Roll in one statement: a whole-row write here would overwrite usage the API
        # added since the snapshot was read. evidence.lockout is the API's; leave it.
        window_start, reset_at = quota_window(free_type, now)
        await repo.roll_window(prov["id"], pool_key, window_start=window_start, reset_at=reset_at)
        record = replace(
            record, window_start=window_start, reset_at=reset_at, used_units=Decimal(0)
        )
    await repo.record_sample(prov["id"], now, record.used_units, record.reset_at)


GATEWAY_DECLARATION = ProviderDeclaration(
    provider_types=frozenset({ProviderType.OPENAI_GATEWAY.value}),
    dedicated_provider_type=True,
    lockout_model_paths=(("gateway", "model_id"),),
    requires_gpu_class=False,
    seed_config=_gateway_seed_config,
    proxy_outbound_headers=_gateway_proxy_headers,
    quota_tick=_gateway_quota_tick,
)


class GatewayProvider:
    id = "openai_gateway"
    name = "OpenAI-compatible gateway"
    credential_schema = GatewayCredentials
    declaration = GATEWAY_DECLARATION
    capabilities = frozenset({ProviderCapability.SYNC_INFERENCE, ProviderCapability.AVAILABILITY})

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._transport = transport
        self._environ = os.environ if environ is None else environ

    def pricing_model(
        self,
        capability: Capability,
        provider_record: ProviderRecord,
    ) -> TaggedPricingModel:
        mode = CostMode(capability.cost_mode)
        pricing = parse_pricing_model(provider_record, cost_mode=mode)
        if not isinstance(pricing, (ZeroOrEnergyPricing, PerTokenPricing)):
            raise ValueError("GatewayProvider requires zero or per_token pricing")
        return pricing

    def _resolve(
        self,
        credentials: CredentialInput,
        provider_record: ProviderRecord,
    ) -> GatewayCredentials:
        """Validate credentials through the shared resolver; a keyless pool has no key."""
        if isinstance(credentials, GatewayCredentials):
            return credentials
        base_url = str(_gateway_config(provider_record)["base_url"])
        candidate: CredentialInput
        if isinstance(credentials, CredentialReference):
            key = self._environ.get(credentials.name, "")
            candidate = {"base_url": base_url, **({"api_key": key} if key else {})}
        else:
            candidate = {**dict(credentials), "base_url": base_url}
        if _gateway_config(provider_record).get("local") is True:
            candidate = {**candidate, "local": True}
        return resolve_adapter_credentials(candidate, GatewayCredentials, adapter_id=self.id)

    async def infer(self, request: InferenceRequest) -> GatewayInferenceResult:
        creds = self._resolve(request.credentials, request.provider_record)
        gateway = _gateway_config(request.provider_record)
        model_id = str(gateway["model_id"])
        body = {**dict(request.payload), "model": model_id}
        headers = _infer_headers(
            creds,
            compression_policy=str(gateway.get("compression", "off") or "off"),
            environ=self._environ,
            route=request.provider_record.name,
        )
        async with httpx.AsyncClient(
            base_url=creds.base_url,
            timeout=creds.timeout_s,
            transport=self._transport,
        ) as client:
            response = await client.post(_CHAT_COMPLETIONS_PATH, headers=headers, json=body)
        now = request.context.now or dt.datetime.now(dt.UTC)
        if response.status_code == 429 or (
            response.status_code in (503, 529) and "capacity" in response.text.lower()
        ):
            reason, reset_at = classify_429(
                response.status_code,
                response.text,
                dict(response.headers),
                now=now,
            )
            raise QuotaExhausted(
                response.status_code,
                response.text,
                reason=reason,
                reset_at=reset_at,
            )
        if response.status_code >= 400:
            raise GatewayProviderError(response.status_code, response.text)
        data = _json_object_response(response)
        usage_value = data.get("usage")
        usage: Mapping[str, Any] = usage_value if isinstance(usage_value, Mapping) else {}
        return GatewayInferenceResult(
            provider_id=request.provider_record.id,
            output=data,
            raw=data,
            model=str(data.get("model", model_id)),
            prompt_tokens=_opt_int(usage.get("prompt_tokens")),
            completion_tokens=_opt_int(usage.get("completion_tokens")),
            total_tokens=_opt_int(usage.get("total_tokens")),
        )

    async def availability(self, request: AvailabilityRequest) -> AvailabilityResult:
        """Catalog evidence is the availability truth; no upstream call (§9.1)."""
        gateway = _gateway_config(request.provider_record)
        catalog = dict(gateway.get("catalog", {}))
        observed_at = (request.context.now or dt.datetime.now(dt.UTC)).astimezone(dt.UTC)
        item = AvailabilityItem(
            provider_id=request.provider_record.id,
            resource_id=str(gateway["model_id"]),
            kind=AvailabilityKind.MODEL,
            available=bool(request.provider_record.enabled),
            attributes={
                key: catalog.get(key)
                for key in (
                    "free_type",
                    "tos",
                    "trains_on_prompts",
                    "hard_stop_guaranteed",
                    "pool_key",
                    "eligibility_gate",
                    "monthly_tokens",
                    "credit_tokens",
                    "display_name",
                )
            },
        )
        return AvailabilityResult(
            provider_id=request.provider_record.id,
            observed_at=observed_at,
            source_contract=CATALOG_SOURCE_CONTRACT,
            items=(item,),
        )


def _gateway_config(provider_record: ProviderRecord) -> Mapping[str, Any]:
    gateway = (
        provider_record.config.get("gateway")
        if isinstance(provider_record.config, Mapping)
        else None
    )
    if (
        not isinstance(gateway, Mapping)
        or not gateway.get("base_url")
        or not gateway.get("model_id")
    ):
        raise ValueError("openai_gateway provider requires config.gateway.base_url and model_id")
    return gateway


def _infer_headers(
    creds: GatewayCredentials,
    *,
    compression_policy: str,
    environ: Mapping[str, str],
    route: str,
) -> dict[str, str]:
    """Loopback (fork) requests authenticate with PITWALL_GATEWAY_TOKEN, carry the
    compression policy, and name the provider route the fork resolves to an upstream;
    direct requests use the pool bearer key and none of the fork-specific headers
    (keyless sends no Authorization at all)."""
    headers = {"Content-Type": "application/json"}
    if is_loopback_base_url(creds.base_url):
        headers.update(_fork_headers(route, compression_policy, environ))
    elif creds.api_key is not None:
        headers["Authorization"] = f"Bearer {creds.api_key.get_secret_value()}"
    return headers


def _fork_headers(
    route: str, compression_policy: str, environ: Mapping[str, str]
) -> dict[str, str]:
    """Headers every request to the loopback fork carries, whatever sends it."""
    headers = {COMPRESSION_HEADER: compression_policy, ROUTE_HEADER: route}
    token = environ.get(FORK_TOKEN_ENV, "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fork_upstream_headers(
    provider_record: Any, *, environ: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Fork headers for a caller that sends to an ``openai_gateway`` provider itself.

    Empty for any provider that is not an ``openai_gateway`` row on the loopback fork,
    so callers can merge the result unconditionally.
    """
    config = getattr(provider_record, "config", None)
    gateway = config.get("gateway") if isinstance(config, Mapping) else None
    if not isinstance(gateway, Mapping) or not is_loopback_base_url(
        str(gateway.get("base_url", ""))
    ):
        return {}
    compression = str(gateway.get("compression", "off") or "off")
    return _fork_headers(
        str(provider_record.name), compression, os.environ if environ is None else environ
    )


def _opt_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, Decimal):
        try:
            integral = value.to_integral_value()
        except ArithmeticError:
            return None
        return int(integral) if integral == value and integral >= 0 else None
    return None


def _json_object_response(response: httpx.Response) -> Mapping[str, Any]:
    try:
        data: object = json.loads(response.text, parse_float=Decimal)
    except json.JSONDecodeError as exc:
        raise GatewayProviderError(response.status_code, "response body was not JSON") from exc
    if not isinstance(data, Mapping):
        raise GatewayProviderError(response.status_code, "response body was not a JSON object")
    return data


def _compact_error_body(body: str) -> str:
    normalized = " ".join(body.split())
    if not normalized:
        return "<empty body>"
    if len(normalized) <= _MAX_ERROR_BODY_CHARS:
        return normalized
    return f"{normalized[:_MAX_ERROR_BODY_CHARS]}..."


__all__ = [
    "CATALOG_SOURCE_CONTRACT",
    "GatewayCredentials",
    "GatewayInferenceResult",
    "GatewayProvider",
    "GatewayProviderError",
    "QuotaExhausted",
    "classify_429",
    "fork_upstream_headers",
    "is_loopback_base_url",
]
