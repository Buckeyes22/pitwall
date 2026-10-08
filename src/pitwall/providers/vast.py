"""Vast.ai provider plugin adapter."""

from __future__ import annotations

import datetime as dt
import logging
import re
import shlex
import uuid
from collections.abc import Mapping
from decimal import Decimal
from functools import partial
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from pitwall.core.enums import LeaseState
from pitwall.core.models import Capability
from pitwall.core.models import Provider as ProviderRecord
from pitwall.cost.estimator import (
    PerSecondPricing,
    TaggedPricingModel,
    parse_pricing_model,
)
from pitwall.providers._lease_compute import (
    _admit_budget,
    _config_mapping,
    _cost_mapping,
    _external_id_for_lease,
    _first_present,
    _has_acquire,
    _hourly_usd_to_per_second,
    _json_dumps,
    _json_safe,
    _mapping_value,
    _non_negative_decimal,
    _normalized_text,
    _optional_non_negative_int,
    _optional_string,
    _persist_created_lease,
    _raw_object,
    _response_payload,
    _rows_affected,
    _utc_now,
    close_lease,
    compensate_failed_provision,
)
from pitwall.providers.interface import (
    AvailabilityItem,
    AvailabilityKind,
    AvailabilityRequest,
    AvailabilityResult,
    CredentialInput,
    ProviderCapability,
    ProviderDeclaration,
    ProvisionRequest,
    ProvisionResult,
    ReconcileRequest,
    ReconcileResult,
    ResourceStatus,
    StatusRequest,
    StatusResult,
    TeardownRequest,
    TeardownResult,
    resolve_adapter_credentials,
)
from pitwall.providers.provisioning import (
    fallback_lease_rate_per_second,
    lease_rate_per_second,
    load_provision_replay,
    mark_provision_completed,
)
from pitwall.providers.runpod import SafeProviderUrl

VAST_API_URL = "https://console.vast.ai/api/v0"
VAST_INSTANCES_API_URL = "https://console.vast.ai/api/v1"
_VAST_INSTANCE_PAGE_LIMIT = 25
_VAST_INSTANCE_MAX_PAGES = 4

_HOUR_SECONDS = Decimal(3600)
_HOURLY_USD_QUANTUM = Decimal("0.000001")
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ACTIVE_LEASE_STATE_VALUES = (
    LeaseState.CREATING.value,
    LeaseState.WAITING_RUNTIME.value,
    LeaseState.WAITING_PROBE.value,
    LeaseState.ACTIVE.value,
    LeaseState.STOPPING.value,
)
_CREATE_FIELD_KEYS = frozenset(
    {
        "args",
        "args_str",
        "cancel_unavail",
        "disk",
        "env",
        "extra",
        "image",
        "image_login",
        "jupyter_dir",
        "jupyter_lab",
        "label",
        "lang_utf8",
        "login",
        "onstart",
        "onstart_cmd",
        "price",
        "python_utf8",
        "runtype",
        "target_state",
        "template_hash_id",
        "user",
        "vm",
    }
)
_PROVISIONING_STATUSES = frozenset(
    {
        "",
        "creating",
        "initializing",
        "loading",
        "pending",
        "provisioning",
        "starting",
    }
)
_RUNNING_STATUSES = frozenset({"ready", "running"})
_TERMINATED_STATUSES = frozenset(
    {
        "deleted",
        "destroyed",
        "exited",
        "offline",
        "stopped",
        "terminated",
    }
)
_FAILED_STATUSES = frozenset({"error", "failed", "unhealthy", "unknown"})
_PREEMPTED_MARKERS = frozenset(
    {
        "evicted",
        "interrupted",
        "outbid",
        "preempted",
        "preempted_by_bid",
    }
)

_close_lease = partial(close_lease, failed_reason="vast_failed")

log = logging.getLogger("pitwall.providers.vast")


class VastCredentials(BaseModel):
    """Credentials required for Vast.ai provider operations."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    api_key: SecretStr = Field(min_length=1)
    vast_api_url: SafeProviderUrl = VAST_API_URL
    vast_instances_api_url: SafeProviderUrl = VAST_INSTANCES_API_URL
    client_id: str = Field(default="me", min_length=1)

    @field_validator("api_key")
    @classmethod
    def _validate_api_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("api_key must be non-empty")
        return value


class VastProviderError(RuntimeError):
    """Raised when the Vast provider cannot complete an operation."""


#: No provider types of its own: rows ride on the shared pod/inference surfaces and take the
#: default OpenAI-proxy, lockout, seed, and reconcile behaviour.
VAST_DECLARATION = ProviderDeclaration()


class VastProvider:
    """Provider plugin backed by Vast.ai's REST API."""

    id = "vast"
    name = "Vast.ai"
    credential_schema = VastCredentials
    declaration = VAST_DECLARATION
    capabilities = frozenset({ProviderCapability.COMPUTE, ProviderCapability.AVAILABILITY})

    def __init__(
        self,
        *,
        timeout_s: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._timeout_s = timeout_s
        self._transport = transport

    def pricing_model(
        self,
        capability: Capability,
        provider_record: ProviderRecord,
    ) -> TaggedPricingModel:
        cost = _cost_mapping(provider_record)
        hourly_rate = _first_present(
            cost,
            "price_per_hour",
            "rate_per_hour",
            "dph_total",
            "on_demand_price_per_hour",
        )
        hourly_bid = _first_present(
            cost,
            "bid_price_per_hour",
            "bid_per_hour",
            "min_bid",
            "spot_price_per_hour",
        )
        # A configured create price is what the create sends as the bid (_create_instance_body),
        # so it is the bid this lease reserves and settles at.
        price_override = _configured_create_price(_config_mapping(provider_record))
        if price_override is not None:
            hourly_bid = price_override
        if hourly_rate is not None:
            return PerSecondPricing(
                rate_per_second=_hourly_usd_to_per_second(hourly_rate, "price_per_hour"),
                bid_rate_per_second=(
                    _hourly_usd_to_per_second(hourly_bid, "bid_price_per_hour")
                    if hourly_bid is not None
                    else None
                ),
            )
        return parse_pricing_model(provider_record, cost_mode=capability.cost_mode)

    async def availability(self, request: AvailabilityRequest) -> AvailabilityResult:
        """Return a bounded normalized snapshot from Vast's v0 offer search."""

        credentials = _vast_credentials(request.credentials)
        raw = await self._json_request(
            credentials,
            "POST",
            "/bundles/",
            json_body={
                "limit": request.limit,
                "type": "on-demand",
                "verified": {"eq": True},
                "rentable": {"eq": True},
                "rented": {"eq": False},
            },
        )
        items = tuple(
            sorted(
                (_availability_item(request.provider_record.id, offer) for offer in _offers(raw)),
                key=lambda item: item.resource_id,
            )
        )
        return AvailabilityResult(
            provider_id=request.provider_record.id,
            observed_at=_utc_now(request.context.now),
            source_contract="vast-api-v0-bundles-2026-09-01",
            items=items,
        )

    async def provision(self, request: ProvisionRequest) -> ProvisionResult:
        credentials = _vast_credentials(request.credentials)
        config = _config_mapping(request.provider_record)
        payload = dict(request.payload)
        pricing = self.pricing_model(request.capability, request.provider_record)
        ask_id = _offer_id(config, payload)
        body = _create_instance_body(
            config,
            payload,
            provider_id=request.provider_record.id,
            request_id=request.request_id,
            extra_env=request.extra_env,
            pricing=pricing,
        )
        _refuse_uncounted_price(body, pricing)
        if request.dry_run:
            return ProvisionResult(
                provider_id=request.provider_record.id,
                external_id=None,
                lease_id=None,
                raw={
                    "backend": self.id,
                    "dry_run": True,
                    "ask_id": ask_id,
                    "create": _json_safe(body),
                },
            )

        admission = await _admit_budget(request, pricing)
        if admission is not None and not admission.is_new:
            replay = await load_provision_replay(
                request.context.pool,
                admission.workload_id,
                capability_id=request.capability.id,
                provider_id=request.provider_record.id,
                request_fingerprint=request.request_fingerprint,
            )
            return ProvisionResult(
                provider_id=request.provider_record.id,
                external_id=replay.external_id,
                lease_id=replay.lease_id,
                raw={"workload_id": admission.workload_id, "idempotent_replay": True},
            )
        workload_id = admission.workload_id if admission is not None else None
        external_id: str | None = None
        lease_id: str | None = None
        dispatch_started = False
        try:
            dispatch_started = True
            raw = await self._json_request(
                credentials,
                "PUT",
                f"/asks/{ask_id}/",
                json_body=body,
            )
            external_id = _created_instance_id(raw)
            if external_id is None:
                raise VastProviderError(
                    "Vast create succeeded but response did not include an external id"
                )
            lease_id = await _persist_created_lease(
                request.context.pool,
                provider_record=request.provider_record,
                external_id=external_id,
                now=request.context.now,
                workload_id=workload_id,
            )
            await mark_provision_completed(
                request.context.pool,
                workload_id=workload_id,
                external_id=external_id,
                lease_id=lease_id,
                now=request.context.now,
            )
        except BaseException:
            await compensate_failed_provision(
                pool=request.context.pool,
                now=request.context.now,
                workload_id=workload_id,
                external_id=external_id,
                lease_id=lease_id,
                dispatch_started=dispatch_started,
                delete_external=lambda resource_id: self._delete_external(credentials, resource_id),
                compensated_reason="vast_provision_compensated",
                failed_reason="vast_failed",
                label="Vast",
                log=log,
            )
            raise
        result_raw = dict(raw)
        if workload_id is not None:
            result_raw["workload_id"] = workload_id
        return ProvisionResult(
            provider_id=request.provider_record.id,
            external_id=external_id,
            lease_id=lease_id,
            raw=result_raw,
        )

    async def _delete_external(self, credentials: VastCredentials, external_id: str) -> None:
        response = await self._request(
            credentials,
            "DELETE",
            f"/instances/{_resource_id(external_id)}/",
        )
        if response.status_code != 404:
            _raise_for_status(response)

    async def status(self, request: StatusRequest) -> StatusResult:
        credentials = _vast_credentials(request.credentials)
        response = await self._request(
            credentials,
            "GET",
            f"/instances/{_resource_id(request.external_id)}/",
        )
        if response.status_code == 404:
            return StatusResult(
                provider_id=request.provider_record.id,
                external_id=request.external_id,
                status=ResourceStatus.TERMINATED,
                raw={},
            )
        _raise_for_status(response)
        data = _response_payload(response)
        instance = _instance_from_payload(data, request.external_id)
        if instance is None:
            return StatusResult(
                provider_id=request.provider_record.id,
                external_id=request.external_id,
                status=ResourceStatus.UNKNOWN,
                raw=_raw_object(data),
            )
        raw = _annotated_instance(instance)
        return StatusResult(
            provider_id=request.provider_record.id,
            external_id=request.external_id,
            status=_resource_status(raw),
            raw=raw,
        )

    async def reconcile(self, request: ReconcileRequest) -> ReconcileResult:
        resources: list[dict[str, Any]]
        if request.external_ids:
            resources = []
            for external_id in request.external_ids:
                status = await self.status(
                    StatusRequest(
                        context=request.context,
                        provider_record=request.provider_record,
                        credentials=request.credentials,
                        external_id=external_id,
                    )
                )
                resource = dict(status.raw)
                if "id" not in resource:
                    resource["id"] = external_id
                resources.append(resource)
        else:
            credentials = _vast_credentials(request.credentials)
            resources = [
                _annotated_instance(item) for item in await self._list_instances(credentials)
            ]

        updated = 0
        for resource in resources:
            resource_external_id = _instance_id(resource)
            if resource_external_id is None or not _is_preempted(resource):
                continue
            updated += await _mark_preempted_failed(
                request.context.pool,
                provider_id=request.provider_record.id,
                external_id=resource_external_id,
                now=request.context.now,
            )

        return ReconcileResult(
            provider_id=request.provider_record.id,
            checked=len(resources),
            updated=updated,
            raw={"resources": resources},
        )

    async def teardown(self, request: TeardownRequest) -> TeardownResult:
        credentials = _vast_credentials(request.credentials)
        external_id = await _external_id_for_lease(request.context.pool, request.lease_id)
        if external_id is None:
            raise VastProviderError(
                f"Vast teardown requires a persisted external id for lease {request.lease_id!r}"
            )

        response = await self._request(
            credentials,
            "DELETE",
            f"/instances/{_resource_id(external_id)}/",
        )
        if response.status_code == 404:
            raw: dict[str, Any] = {"success": True, "already_absent": True}
        else:
            _raise_for_status(response)
            raw_payload = _response_payload(response)
            raw = _raw_object(raw_payload)

        updated = await _close_lease(
            request.context.pool,
            lease_id=request.lease_id,
            terminal_state=request.terminal_state,
            reason=request.reason,
            now=request.context.now,
        )
        if updated:
            raw["lease_updated"] = updated
        return TeardownResult(
            provider_id=request.provider_record.id,
            lease_id=request.lease_id,
            external_id=external_id,
            raw=raw,
        )

    async def _request(
        self,
        credentials: VastCredentials,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        base_url: str | None = None,
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {credentials.api_key.get_secret_value()}"}
        content: str | None = None
        if json_body is not None:
            headers["Content-Type"] = "application/json"
            content = _json_dumps(json_body)
        async with httpx.AsyncClient(
            base_url=base_url or credentials.vast_api_url,
            timeout=self._timeout_s,
            transport=self._transport,
        ) as client:
            return await client.request(
                method,
                path,
                headers=headers,
                content=content,
                params=params,
            )

    async def _json_request(
        self,
        credentials: VastCredentials,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        base_url: str | None = None,
    ) -> dict[str, Any]:
        response = await self._request(
            credentials,
            method,
            path,
            json_body=json_body,
            params=params,
            base_url=base_url,
        )
        _raise_for_status(response)
        return _raw_object(_response_payload(response))

    async def _list_instances(self, credentials: VastCredentials) -> list[dict[str, Any]]:
        """Read the current v1 keyset-paginated instance collection, bounded to 100."""

        instances: list[dict[str, Any]] = []
        after_token: str | None = None
        seen_tokens: set[str] = set()
        for _page in range(_VAST_INSTANCE_MAX_PAGES):
            params: dict[str, Any] = {"limit": _VAST_INSTANCE_PAGE_LIMIT}
            if after_token is not None:
                params["after_token"] = after_token
            raw = await self._json_request(
                credentials,
                "GET",
                "/instances/",
                params=params,
                base_url=credentials.vast_instances_api_url,
            )
            instances.extend(_instances_from_payload(raw))
            raw_next = raw.get("next_token")
            if raw_next is None:
                return instances
            if not isinstance(raw_next, str) or not raw_next.strip():
                raise VastProviderError("Vast v1 instance pagination returned an invalid cursor")
            after_token = raw_next.strip()
            if after_token in seen_tokens:
                raise VastProviderError("Vast v1 instance pagination repeated a cursor")
            seen_tokens.add(after_token)
        raise VastProviderError("Vast v1 instance pagination exceeded the 100-instance bound")


async def _mark_preempted_failed(
    pool: Any,
    *,
    provider_id: str,
    external_id: str,
    now: dt.datetime | None,
) -> int:
    if not _has_acquire(pool):
        return 0
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE pitwall.leases
            SET state = $1,
                terminated_at = $2,
                terminated_reason = $3
            WHERE provider_id = $4
              AND external_resource_id = $5
              AND state = ANY($6::text[])
            """,
            LeaseState.FAILED.value,
            _utc_now(now),
            "vast_preempted",
            provider_id,
            external_id,
            list(_ACTIVE_LEASE_STATE_VALUES),
        )
    return _rows_affected(result)


def _create_instance_body(
    config: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    provider_id: str,
    request_id: str | None,
    extra_env: Mapping[str, str] | None,
    pricing: TaggedPricingModel,
) -> dict[str, Any]:
    body: dict[str, Any] = {}
    body.update(_mapping_value(config.get("create")))
    body.update(_mapping_value(payload.get("vast_create")))
    body.update(_mapping_value(payload.get("instance")))
    body.update(_mapping_value(payload.get("create")))

    for key in _CREATE_FIELD_KEYS:
        if key in config and key not in body:
            body[key] = config[key]
        if key in payload:
            body[key] = payload[key]

    if "label" not in body:
        body["label"] = _default_label(provider_id, request_id)
    if "price" not in body:
        bid_price = _bid_price_per_hour(config, pricing)
        if bid_price is not None:
            body["price"] = bid_price.quantize(_HOURLY_USD_QUANTUM)

    env_flags = _env_flags(extra_env)
    if env_flags:
        configured_env = body.get("env")
        if isinstance(configured_env, str) and configured_env.strip():
            body["env"] = f"{configured_env.strip()} {env_flags}"
        else:
            body["env"] = env_flags

    if not _optional_string(body.get("image")) and not _optional_string(
        body.get("template_hash_id")
    ):
        raise VastProviderError("Vast provision requires create.image or create.template_hash_id")
    return body


def _offer_id(config: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    raw = _first_present(payload, "ask_id", "offer_id")
    if raw is None:
        raw = _first_present(config, "ask_id", "offer_id")
    value = _optional_string(raw)
    if value is None:
        raise VastProviderError("Vast provision requires ask_id or offer_id")
    if not value.isdigit():
        raise VastProviderError("Vast ask_id/offer_id must contain only digits")
    return value


def _configured_create_price(config: Mapping[str, Any]) -> object | None:
    """The hourly ``price`` provider config sends on create (``create.price``, then ``price``)."""
    create = _mapping_value(config.get("create"))
    price: object | None = create.get("price")
    if price is None:
        price = config.get("price")
    return price


def _refuse_uncounted_price(body: Mapping[str, Any], pricing: TaggedPricingModel) -> None:
    """Refuse a create whose sent price is above the rate the lease reserves and settles at.

    Config prices are folded into the pricing; a request payload price above it would bill
    more than the budget counts, so it is refused before admission or any provider call.
    """
    sent = body.get("price")
    if sent is None:
        return
    sent_rate = _hourly_usd_to_per_second(sent, "price")
    counted = lease_rate_per_second(pricing) or fallback_lease_rate_per_second()
    if sent_rate > counted:
        raise VastProviderError(
            "Vast create price is above the provider's configured rate; set it in the provider "
            "config (cost or create.price) so the lease budget counts it"
        )


def _bid_price_per_hour(
    config: Mapping[str, Any],
    pricing: TaggedPricingModel,
) -> Decimal | None:
    cost = _mapping_value(config.get("cost"))
    raw = _first_present(
        cost,
        "bid_price_per_hour",
        "bid_per_hour",
        "min_bid",
        "spot_price_per_hour",
    )
    if raw is not None:
        return _non_negative_decimal(raw, "bid_price_per_hour")
    if isinstance(pricing, PerSecondPricing) and pricing.bid_rate_per_second is not None:
        return pricing.bid_rate_per_second * _HOUR_SECONDS
    return None


def _offers(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = payload.get("offers")
    if isinstance(raw, Mapping):
        return [raw]
    if isinstance(raw, list) and all(isinstance(item, Mapping) for item in raw):
        return list(raw)
    raise VastProviderError("Vast offer search returned an invalid offers envelope")


def _availability_item(provider_id: str, offer: Mapping[str, Any]) -> AvailabilityItem:
    resource_id = _optional_string(_first_present(offer, "id", "ask_contract_id"))
    if resource_id is None:
        raise VastProviderError("Vast offer did not include an id")
    pricing: dict[str, Decimal] = {}
    hourly = _first_present(offer, "dph_total_adj", "dph_total", "discounted_dph_total")
    search = offer.get("search")
    if hourly is None and isinstance(search, Mapping):
        hourly = _first_present(search, "totalHour", "discountedTotalPerHour")
    if hourly is not None:
        pricing["usd_per_hour"] = _non_negative_decimal(hourly, "offer.dph_total")
    minimum_bid = offer.get("min_bid")
    if minimum_bid is not None:
        pricing["usd_min_bid_per_hour"] = _non_negative_decimal(
            minimum_bid,
            "offer.min_bid",
        )
    accelerator_count = _optional_non_negative_int(offer.get("num_gpus"))
    available = _optional_bool(offer.get("rentable"))
    rented = _optional_bool(offer.get("rented"))
    if available is not None and rented is not None:
        available = available and not rented
    return AvailabilityItem(
        provider_id=provider_id,
        resource_id=resource_id,
        kind=AvailabilityKind.COMPUTE,
        available=available,
        region=_optional_string(_first_present(offer, "geolocation", "country", "location")),
        accelerator=_optional_string(_first_present(offer, "gpu_name", "gpu_display_name")),
        accelerator_count=accelerator_count,
        pricing=pricing,
        attributes={
            key: value
            for key, value in {
                "reliability": offer.get("reliability"),
                "gpu_ram_mb": offer.get("gpu_ram"),
                "verification": offer.get("verification"),
            }.items()
            if value is not None
        },
    )


def _instances_from_payload(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    instances = payload.get("instances")
    if isinstance(instances, list):
        return [dict(item) for item in instances if isinstance(item, Mapping)]
    if isinstance(instances, Mapping):
        return [dict(instances)]
    if _looks_like_instance(payload):
        return [dict(payload)]
    return []


def _instance_from_payload(payload: object, external_id: str) -> dict[str, Any] | None:
    if not isinstance(payload, Mapping):
        return None
    instances = payload.get("instances")
    if isinstance(instances, Mapping):
        return dict(instances)
    if isinstance(instances, list):
        for item in instances:
            if not isinstance(item, Mapping):
                continue
            item_id = _instance_id(item)
            if item_id == external_id:
                return dict(item)
        return None
    if _looks_like_instance(payload):
        return dict(payload)
    return None


def _annotated_instance(instance: Mapping[str, Any]) -> dict[str, Any]:
    raw = dict(instance)
    if _is_preempted(raw):
        raw["pitwall_preempted"] = True
        raw["pitwall_safe_state"] = LeaseState.FAILED.value
    return raw


def _resource_status(instance: Mapping[str, Any]) -> ResourceStatus:
    status = _status_text(instance)
    if _is_preempted(instance):
        return ResourceStatus.FAILED
    if status in _RUNNING_STATUSES:
        return ResourceStatus.RUNNING
    if status in _PROVISIONING_STATUSES:
        return ResourceStatus.PROVISIONING
    if status in _TERMINATED_STATUSES:
        return ResourceStatus.TERMINATED
    if status in _FAILED_STATUSES:
        return ResourceStatus.FAILED
    return ResourceStatus.UNKNOWN


def _is_preempted(instance: Mapping[str, Any]) -> bool:
    values = (
        _status_text(instance),
        _normalized_text(instance.get("status_msg")),
        _normalized_text(instance.get("status_message")),
    )
    return any(marker in value for marker in _PREEMPTED_MARKERS for value in values)


def _status_text(instance: Mapping[str, Any]) -> str:
    for key in ("actual_status", "cur_state", "next_state", "status", "state", "intended_status"):
        value = _normalized_text(instance.get(key))
        if value:
            return value
    return ""


def _instance_id(instance: Mapping[str, Any]) -> str | None:
    for key in ("id", "new_contract", "contract_id", "instance_id"):
        value = _optional_string(instance.get(key))
        if value is not None:
            return value
    return None


def _created_instance_id(payload: Mapping[str, Any]) -> str | None:
    for key in ("new_contract", "contract_id", "instance_id", "id"):
        value = _optional_string(payload.get(key))
        if value is not None:
            return value
    return None


def _looks_like_instance(payload: Mapping[str, Any]) -> bool:
    return any(key in payload for key in ("actual_status", "cur_state", "status", "state"))


def _raise_for_status(response: httpx.Response) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise VastProviderError(str(exc)) from exc


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _env_flags(extra_env: Mapping[str, str] | None) -> str:
    if not extra_env:
        return ""
    flags: list[str] = []
    for key, value in extra_env.items():
        if not _ENV_KEY_RE.fullmatch(key):
            raise VastProviderError(f"extra_env contains invalid env key {key!r}")
        flags.append(f"-e {key}={shlex.quote(str(value))}")
    return " ".join(flags)


def _default_label(provider_id: str, request_id: str | None) -> str:
    if request_id is not None and request_id.strip():
        suffix = request_id.strip()
    else:
        suffix = uuid.uuid4().hex[:12]
    return f"pitwall-{provider_id}-{suffix}"


def _resource_id(value: str) -> str:
    stripped = value.strip()
    if not stripped or "/" in stripped:
        raise VastProviderError("Vast resource id must be non-empty and contain no slashes")
    return stripped


def _vast_credentials(credentials: CredentialInput) -> VastCredentials:
    return resolve_adapter_credentials(credentials, VastCredentials, adapter_id="vast")


__all__ = [
    "VAST_API_URL",
    "VAST_INSTANCES_API_URL",
    "VastCredentials",
    "VastProvider",
    "VastProviderError",
]
