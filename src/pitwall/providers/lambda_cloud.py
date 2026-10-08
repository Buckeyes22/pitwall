"""Lambda Cloud provider plugin adapter."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from decimal import Decimal
from functools import partial
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from pitwall.core.enums import LeaseState
from pitwall.core.models import Capability
from pitwall.core.models import Provider as ProviderRecord
from pitwall.cost.estimator import (
    PerVmSecondPricing,
    TaggedPricingModel,
    parse_pricing_model,
)
from pitwall.providers._lease_compute import (
    IntervalGate,
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
    _positive_int,
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
    load_provision_replay,
    mark_provision_completed,
)
from pitwall.providers.runpod import SafeProviderUrl

LAMBDA_CLOUD_API_URL = "https://cloud.lambda.ai/api/v1"
LAMBDA_CLOUD_LAUNCH_INTERVAL_S = 12.0
LAMBDA_CLOUD_REQUEST_INTERVAL_S = 1.0

_ACTIVE_LEASE_STATE_VALUES = (
    LeaseState.CREATING.value,
    LeaseState.WAITING_RUNTIME.value,
    LeaseState.WAITING_PROBE.value,
    LeaseState.ACTIVE.value,
    LeaseState.STOPPING.value,
)
_LAUNCH_FIELD_KEYS = frozenset(
    {
        "file_system_mounts",
        "file_system_names",
        "firewall_rulesets",
        "hostname",
        "image",
        "instance_type_name",
        "name",
        "quantity",
        "region_name",
        "ssh_key_names",
        "tags",
        "user_data",
    }
)
_PROVISIONING_STATUSES = frozenset(
    {"", "booting", "creating", "launching", "pending", "provisioning", "starting"}
)
_RUNNING_STATUSES = frozenset({"active", "ready", "running"})
_TERMINATED_STATUSES = frozenset({"deleted", "terminated", "terminating"})
_FAILED_STATUSES = frozenset({"error", "failed", "preempted", "unhealthy"})
_PREEMPTED_STATUSES = frozenset({"preempted"})

_close_lease = partial(close_lease, failed_reason="lambda_cloud_failed")

log = logging.getLogger("pitwall.providers.lambda_cloud")


class LambdaCloudCredentials(BaseModel):
    """Credentials required for Lambda Cloud provider operations."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    api_key: SecretStr = Field(min_length=1)
    lambda_api_url: SafeProviderUrl = LAMBDA_CLOUD_API_URL

    @field_validator("api_key")
    @classmethod
    def _validate_api_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("api_key must be non-empty")
        return value


class LambdaCloudProviderError(RuntimeError):
    """Raised when the Lambda Cloud provider cannot complete an operation."""


#: No provider types of its own: rows ride on the shared pod/inference surfaces and take the
#: default OpenAI-proxy, lockout, seed, and reconcile behaviour.
LAMBDA_CLOUD_DECLARATION = ProviderDeclaration()


class LambdaCloudProvider:
    """Provider plugin backed by Lambda Cloud's REST API."""

    id = "lambda_cloud"
    name = "Lambda Cloud"
    credential_schema = LambdaCloudCredentials
    declaration = LAMBDA_CLOUD_DECLARATION
    capabilities = frozenset({ProviderCapability.COMPUTE, ProviderCapability.AVAILABILITY})

    def __init__(
        self,
        *,
        timeout_s: float = 60.0,
        launch_interval_s: float = LAMBDA_CLOUD_LAUNCH_INTERVAL_S,
        request_interval_s: float = LAMBDA_CLOUD_REQUEST_INTERVAL_S,
        transport: httpx.AsyncBaseTransport | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if launch_interval_s < 0:
            raise ValueError("launch_interval_s must be non-negative")
        if request_interval_s < 0:
            raise ValueError("request_interval_s must be non-negative")
        self._timeout_s = timeout_s
        self._transport = transport
        self._launch_gate = IntervalGate(launch_interval_s, monotonic=monotonic, sleeper=sleeper)
        self._request_gate = IntervalGate(request_interval_s, monotonic=monotonic, sleeper=sleeper)

    def pricing_model(
        self,
        capability: Capability,
        provider_record: ProviderRecord,
    ) -> TaggedPricingModel:
        cost = _cost_mapping(provider_record)
        rate_per_second = _first_present(
            cost,
            "rate_per_second",
            "per_vm_second",
            "price_per_second",
            "price_usd_per_second",
        )
        if rate_per_second is not None:
            return PerVmSecondPricing(
                rate_per_second=_non_negative_decimal(rate_per_second, "rate_per_second")
            )

        hourly_rate = _first_present(
            cost,
            "price_per_hour",
            "rate_per_hour",
            "price_usd_per_hour",
        )
        if hourly_rate is not None:
            return PerVmSecondPricing(
                rate_per_second=_hourly_usd_to_per_second(hourly_rate, "price_per_hour")
            )

        pricing = parse_pricing_model(provider_record, cost_mode=capability.cost_mode)
        if not isinstance(pricing, PerVmSecondPricing):
            raise LambdaCloudProviderError("Lambda Cloud provider requires per_vm_second pricing")
        return pricing

    async def availability(self, request: AvailabilityRequest) -> AvailabilityResult:
        """Return Lambda's current instance catalogue and capacity regions."""

        credentials = _lambda_cloud_credentials(request.credentials)
        raw = await self._json_request(credentials, "GET", "/instance-types")
        items = tuple(
            sorted(
                (
                    item
                    for name, value in _instance_types(raw)[: request.limit]
                    for item in _availability_items(request.provider_record.id, name, value)
                ),
                key=lambda item: (item.resource_id, item.region or ""),
            )
        )
        return AvailabilityResult(
            provider_id=request.provider_record.id,
            observed_at=_utc_now(request.context.now),
            source_contract="lambda-cloud-openapi-1.10.0",
            items=items,
        )

    async def provision(self, request: ProvisionRequest) -> ProvisionResult:
        credentials = _lambda_cloud_credentials(request.credentials)
        pricing = self.pricing_model(request.capability, request.provider_record)
        body = _launch_instance_body(
            request.provider_record,
            request.payload,
            request_id=request.request_id,
        )
        if request.dry_run:
            return ProvisionResult(
                provider_id=request.provider_record.id,
                external_id=None,
                lease_id=None,
                raw={
                    "backend": self.id,
                    "dry_run": True,
                    "launch": _json_safe(body),
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
        created_ids: list[str] = []
        try:
            await self._launch_gate.wait()
            dispatch_started = True
            raw = await self._json_request(
                credentials,
                "POST",
                "/instance-operations/launch",
                json_body=body,
            )
            created_ids = _created_instance_ids(raw)
            if not created_ids:
                raise LambdaCloudProviderError(
                    "Lambda Cloud launch succeeded but response did not include an external id"
                )
            external_id = created_ids[0]
            if len(created_ids) > 1:
                # The launch asked for one instance. Compensation terminates every returned id.
                raise LambdaCloudProviderError(
                    f"Lambda Cloud launched {len(created_ids)} instances for a single-instance "
                    "request; every returned instance was terminated"
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
                delete_external=lambda resource_id: self._terminate_instances(
                    credentials, created_ids or [resource_id]
                ),
                compensated_reason="lambda_cloud_provision_compensated",
                failed_reason="lambda_cloud_failed",
                label="Lambda Cloud",
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

    async def _terminate_instances(
        self,
        credentials: LambdaCloudCredentials,
        external_ids: Sequence[str],
    ) -> None:
        # The ids travel in a JSON body, not a URL path: only emptiness is checked, and one odd
        # id never keeps the others from being terminated. Log the position, never the value.
        instance_ids: list[str] = []
        for position, item in enumerate(external_ids):
            stripped = item.strip() if isinstance(item, str) else ""
            if stripped:
                instance_ids.append(stripped)
            else:
                log.warning("Lambda Cloud terminate skipped an empty id at position %d", position)
        if not instance_ids:
            return
        response = await self._request(
            credentials,
            "POST",
            "/instance-operations/terminate",
            json_body={"instance_ids": instance_ids},
        )
        if response.status_code != 404:
            _raise_for_status(response)

    async def status(self, request: StatusRequest) -> StatusResult:
        credentials = _lambda_cloud_credentials(request.credentials)
        response = await self._request(
            credentials,
            "GET",
            f"/instances/{_resource_id(request.external_id)}",
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
            credentials = _lambda_cloud_credentials(request.credentials)
            raw = await self._json_request(credentials, "GET", "/instances")
            resources = [_annotated_instance(item) for item in _instances_from_payload(raw)]

        updated = 0
        for resource in resources:
            resource_external_id = _instance_id(resource)
            if resource_external_id is None or not _should_mark_failed(resource):
                continue
            updated += await _mark_failed(
                request.context.pool,
                provider_id=request.provider_record.id,
                external_id=resource_external_id,
                reason=_failure_reason(resource),
                now=request.context.now,
            )

        return ReconcileResult(
            provider_id=request.provider_record.id,
            checked=len(resources),
            updated=updated,
            raw={"resources": resources},
        )

    async def teardown(self, request: TeardownRequest) -> TeardownResult:
        credentials = _lambda_cloud_credentials(request.credentials)
        external_id = await _external_id_for_lease(request.context.pool, request.lease_id)
        if external_id is None:
            raise LambdaCloudProviderError(
                "Lambda Cloud teardown requires a persisted external id "
                f"for lease {request.lease_id!r}"
            )

        response = await self._request(
            credentials,
            "POST",
            "/instance-operations/terminate",
            json_body={"instance_ids": [_resource_id(external_id)]},
        )
        if response.status_code == 404:
            raw: dict[str, Any] = {"success": True, "already_absent": True}
        else:
            _raise_for_status(response)
            raw = _raw_object(_response_payload(response))

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
        credentials: LambdaCloudCredentials,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
    ) -> httpx.Response:
        await self._request_gate.wait()
        headers = {"Authorization": f"Bearer {credentials.api_key.get_secret_value()}"}
        content: str | None = None
        if json_body is not None:
            headers["Content-Type"] = "application/json"
            content = _json_dumps(json_body)
        async with httpx.AsyncClient(
            base_url=credentials.lambda_api_url,
            timeout=self._timeout_s,
            transport=self._transport,
        ) as client:
            return await client.request(method, path, headers=headers, content=content)

    async def _json_request(
        self,
        credentials: LambdaCloudCredentials,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = await self._request(credentials, method, path, json_body=json_body)
        _raise_for_status(response)
        return _raw_object(_response_payload(response))


async def _mark_failed(
    pool: Any,
    *,
    provider_id: str,
    external_id: str,
    reason: str,
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
            reason,
            provider_id,
            external_id,
            list(_ACTIVE_LEASE_STATE_VALUES),
        )
    return _rows_affected(result)


def _launch_instance_body(
    provider_record: ProviderRecord,
    payload: Mapping[str, Any],
    *,
    request_id: str | None,
) -> dict[str, Any]:
    config = _config_mapping(provider_record)
    body: dict[str, Any] = {}
    body.update(_mapping_value(config.get("launch")))
    body.update(_mapping_value(payload.get("lambda_launch")))
    body.update(_mapping_value(payload.get("instance")))
    body.update(_mapping_value(payload.get("launch")))

    for key in _LAUNCH_FIELD_KEYS:
        if key in config and key not in body:
            body[key] = config[key]
        if key in payload:
            body[key] = payload[key]

    if "region_name" not in body and provider_record.region is not None:
        body["region_name"] = provider_record.region
    if "name" not in body:
        body["name"] = _default_name(provider_record.id, request_id)

    _require_non_empty_string(body, "region_name")
    _require_non_empty_string(body, "instance_type_name")
    body["ssh_key_names"] = _non_empty_string_list(body.get("ssh_key_names"), "ssh_key_names")
    if "quantity" in body:
        # One lease records one external resource, so a launch creates exactly one instance.
        try:
            quantity = _positive_int(body["quantity"], "quantity")
        except ValueError:
            quantity = 0
        if quantity != 1:
            raise LambdaCloudProviderError(
                "Lambda Cloud launch quantity must be 1: a lease records one instance"
            )
        body["quantity"] = 1
    return body


def _instance_types(payload: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise LambdaCloudProviderError(
            "Lambda Cloud instance-types returned an invalid data envelope"
        )
    items: list[tuple[str, Mapping[str, Any]]] = []
    for raw_name, value in data.items():
        if not isinstance(raw_name, str) or not raw_name.strip() or not isinstance(value, Mapping):
            raise LambdaCloudProviderError("Lambda Cloud instance-types contained an invalid item")
        items.append((raw_name.strip(), value))
    return sorted(items, key=lambda item: item[0])


def _availability_items(
    provider_id: str,
    fallback_name: str,
    value: Mapping[str, Any],
) -> list[AvailabilityItem]:
    raw_type = value.get("instance_type")
    if not isinstance(raw_type, Mapping):
        raise LambdaCloudProviderError("Lambda Cloud instance type omitted instance_type")
    type_name = _optional_string(raw_type.get("name")) or fallback_name
    raw_price = raw_type.get("price_cents_per_hour")
    if raw_price is None:
        raise LambdaCloudProviderError("Lambda Cloud instance type omitted price_cents_per_hour")
    price_usd = _non_negative_decimal(raw_price, "price_cents_per_hour") / Decimal(100)
    specs_value = raw_type.get("specs")
    specs = specs_value if isinstance(specs_value, Mapping) else {}
    accelerator_count = _optional_non_negative_int(specs.get("gpus"))
    regions_value = value.get("regions_with_capacity_available")
    if not isinstance(regions_value, list):
        raise LambdaCloudProviderError(
            "Lambda Cloud instance type omitted regions_with_capacity_available"
        )
    regions: list[tuple[str, str | None]] = []
    for region in regions_value:
        if not isinstance(region, Mapping):
            raise LambdaCloudProviderError("Lambda Cloud capacity region was invalid")
        name = _optional_string(region.get("name"))
        if name is None:
            raise LambdaCloudProviderError("Lambda Cloud capacity region omitted name")
        regions.append((name, _optional_string(region.get("description"))))
    if not regions:
        regions.append(("", None))
    return [
        AvailabilityItem(
            provider_id=provider_id,
            resource_id=f"{type_name}@{region_name}" if region_name else type_name,
            kind=AvailabilityKind.COMPUTE,
            available=bool(region_name),
            region=region_name or None,
            accelerator=_optional_string(raw_type.get("gpu_description")),
            accelerator_count=accelerator_count,
            pricing={"usd_per_hour": price_usd},
            attributes={
                key: item
                for key, item in {
                    "instance_type": type_name,
                    "description": raw_type.get("description"),
                    "architecture": raw_type.get("architecture"),
                    "region_description": region_description,
                    "vcpus": specs.get("vcpus"),
                    "memory_gib": specs.get("memory_gib"),
                    "storage_gib": specs.get("storage_gib"),
                }.items()
                if item is not None
            },
        )
        for region_name, region_description in sorted(regions)
    ]


def _instances_from_payload(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    if isinstance(data, list):
        return [dict(item) for item in data if isinstance(item, Mapping)]
    if isinstance(data, Mapping):
        instances = data.get("instances")
        if isinstance(instances, list):
            return [dict(item) for item in instances if isinstance(item, Mapping)]
        if isinstance(instances, Mapping):
            return [dict(instances)]
        if _looks_like_instance(data):
            return [dict(data)]
    if _looks_like_instance(payload):
        return [dict(payload)]
    return []


def _instance_from_payload(payload: object, external_id: str) -> dict[str, Any] | None:
    if not isinstance(payload, Mapping):
        return None
    data = payload.get("data")
    if isinstance(data, Mapping):
        instance = _instance_from_mapping(data, external_id)
        if instance is not None:
            return instance
    if isinstance(data, list):
        for item in data:
            if isinstance(item, Mapping) and _instance_id(item) == external_id:
                return dict(item)
        return None
    return _instance_from_mapping(payload, external_id)


def _instance_from_mapping(
    payload: Mapping[str, Any],
    external_id: str,
) -> dict[str, Any] | None:
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
    if status in _RUNNING_STATUSES:
        return ResourceStatus.RUNNING
    if status in _PROVISIONING_STATUSES:
        return ResourceStatus.PROVISIONING
    if status in _TERMINATED_STATUSES:
        return ResourceStatus.TERMINATED
    if status in _FAILED_STATUSES:
        return ResourceStatus.FAILED
    return ResourceStatus.UNKNOWN


def _should_mark_failed(instance: Mapping[str, Any]) -> bool:
    return _resource_status(instance) == ResourceStatus.FAILED


def _is_preempted(instance: Mapping[str, Any]) -> bool:
    return _status_text(instance) in _PREEMPTED_STATUSES


def _failure_reason(instance: Mapping[str, Any]) -> str:
    if _is_preempted(instance):
        return "lambda_cloud_preempted"
    return "lambda_cloud_failed"


def _status_text(instance: Mapping[str, Any]) -> str:
    for key in ("status", "state", "lifecycle_state"):
        value = _normalized_text(instance.get(key))
        if value:
            return value
    return ""


def _instance_id(instance: Mapping[str, Any]) -> str | None:
    for key in ("id", "instance_id"):
        value = _optional_string(instance.get(key))
        if value is not None:
            return value
    return None


def _created_instance_ids(payload: Mapping[str, Any]) -> list[str]:
    """Every instance id a launch response names, in response order."""

    data = payload.get("data")
    if isinstance(data, Mapping):
        instance_ids = data.get("instance_ids")
        if isinstance(instance_ids, Sequence) and not isinstance(
            instance_ids, (bytes, bytearray, str)
        ):
            found = [value for item in instance_ids if (value := _optional_string(item))]
            if found:
                return found
        instance = data.get("instance")
        if isinstance(instance, Mapping):
            value = _instance_id(instance)
            if value is not None:
                return [value]
    for key in ("instance_id", "id"):
        value = _optional_string(payload.get(key))
        if value is not None:
            return [value]
    return []


def _looks_like_instance(payload: Mapping[str, Any]) -> bool:
    return any(key in payload for key in ("id", "instance_id", "status", "state"))


def _raise_for_status(response: httpx.Response) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise LambdaCloudProviderError(str(exc)) from exc


def _require_non_empty_string(mapping: Mapping[str, Any], key: str) -> str:
    value = _optional_string(mapping.get(key))
    if value is None:
        raise LambdaCloudProviderError(f"Lambda Cloud launch requires {key}")
    return value


def _non_empty_string_list(value: object, name: str) -> list[str]:
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        items = [_optional_string(item) for item in value]
        strings = [item for item in items if item is not None]
        if strings:
            return strings
    raise LambdaCloudProviderError(f"Lambda Cloud launch requires non-empty {name}")


def _default_name(provider_id: str, request_id: str | None) -> str:
    if request_id is not None and request_id.strip():
        suffix = request_id.strip()
    else:
        suffix = uuid.uuid4().hex[:12]
    return f"pitwall-{provider_id}-{suffix}"[:64]


def _resource_id(value: str) -> str:
    stripped = value.strip()
    if not stripped or "/" in stripped:
        raise LambdaCloudProviderError(
            "Lambda Cloud resource id must be non-empty and contain no slashes"
        )
    return stripped


def _lambda_cloud_credentials(credentials: CredentialInput) -> LambdaCloudCredentials:
    return resolve_adapter_credentials(
        credentials,
        LambdaCloudCredentials,
        adapter_id="lambda_cloud",
    )


__all__ = [
    "LAMBDA_CLOUD_API_URL",
    "LAMBDA_CLOUD_LAUNCH_INTERVAL_S",
    "LambdaCloudCredentials",
    "LambdaCloudProvider",
    "LambdaCloudProviderError",
]
