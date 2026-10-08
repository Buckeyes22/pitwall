from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import BaseModel, ConfigDict, SecretStr

from pitwall.core.enums import WorkloadState
from pitwall.cost.estimator import GpuHourPricing, TaggedPricingModel
from pitwall.providers import (
    AsyncInferenceCancelProvider,
    AsyncInferenceCancelRequest,
    AsyncInferenceCancelResult,
    AsyncInferenceProvider,
    AsyncInferenceRequest,
    AsyncInferenceStatusProvider,
    AsyncInferenceStatusRequest,
    AsyncInferenceStatusResult,
    AsyncInferenceSubmission,
    ComputeProvider,
    CredentialReference,
    InferenceProvider,
    InferenceRequest,
    InferenceResult,
    InvalidProviderRegistrationError,
    ProviderCapability,
    ProviderRegistry,
    ProvisionRequest,
    ProvisionResult,
    ReconcileRequest,
    ReconcileResult,
    ResourceStatus,
    StatusRequest,
    StatusResult,
    TeardownRequest,
    TeardownResult,
    UnsupportedProviderCapabilityError,
    create_default_registry,
)
from pitwall.providers.interface import ProviderDeclaration


class FakeCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: SecretStr


class FakeMetadata:
    id = "fake"
    name = "Fake"
    credential_schema = FakeCredentials
    declaration = ProviderDeclaration()
    capabilities: frozenset[ProviderCapability] = frozenset()

    def pricing_model(self, capability: object, provider_record: object) -> TaggedPricingModel:
        return GpuHourPricing(per_second_active=Decimal("0"))


class ComputeFake(FakeMetadata):
    capabilities = frozenset({ProviderCapability.COMPUTE})

    async def provision(self, request: ProvisionRequest) -> ProvisionResult:
        return ProvisionResult(request.provider_record.id, "resource-1", "lease-1")

    async def status(self, request: StatusRequest) -> StatusResult:
        return StatusResult(
            request.provider_record.id,
            request.external_id,
            ResourceStatus.RUNNING,
        )

    async def reconcile(self, request: ReconcileRequest) -> ReconcileResult:
        return ReconcileResult(request.provider_record.id, len(request.external_ids), 0)

    async def teardown(self, request: TeardownRequest) -> TeardownResult:
        return TeardownResult(request.provider_record.id, request.lease_id, "resource-1")


class InferenceFake(FakeMetadata):
    id = "inference_fake"
    capabilities = frozenset({ProviderCapability.SYNC_INFERENCE})

    async def infer(self, request: InferenceRequest) -> InferenceResult:
        return InferenceResult(provider_id=request.provider_record.id, output={"ok": True})


class AsyncFake(FakeMetadata):
    id = "async_fake"
    capabilities = frozenset(
        {
            ProviderCapability.ASYNC_INFERENCE,
            ProviderCapability.ASYNC_STATUS,
            ProviderCapability.ASYNC_CANCEL,
        }
    )

    async def submit(self, request: AsyncInferenceRequest) -> AsyncInferenceSubmission:
        return AsyncInferenceSubmission(
            provider_id=request.provider_record.id,
            external_job_id="job-1",
            state=WorkloadState.QUEUED,
        )

    async def job_status(
        self,
        request: AsyncInferenceStatusRequest,
    ) -> AsyncInferenceStatusResult:
        return AsyncInferenceStatusResult(
            provider_id=request.provider_record.id,
            external_job_id=request.external_job_id,
            state=WorkloadState.RUNNING,
        )

    async def cancel_job(
        self,
        request: AsyncInferenceCancelRequest,
    ) -> AsyncInferenceCancelResult:
        return AsyncInferenceCancelResult(
            provider_id=request.provider_record.id,
            external_job_id=request.external_job_id,
            cancelled=True,
        )


class SubmitOnlyFake(FakeMetadata):
    id = "submit_only_fake"
    capabilities = frozenset({ProviderCapability.ASYNC_INFERENCE})

    async def submit(self, request: AsyncInferenceRequest) -> AsyncInferenceSubmission:
        return AsyncInferenceSubmission(
            provider_id=request.provider_record.id,
            external_job_id="job-1",
            state=WorkloadState.QUEUED,
        )


class DualFake(ComputeFake, InferenceFake, AsyncFake):
    id = "dual_fake"
    capabilities = frozenset(
        {
            ProviderCapability.COMPUTE,
            ProviderCapability.SYNC_INFERENCE,
            ProviderCapability.ASYNC_INFERENCE,
            ProviderCapability.ASYNC_STATUS,
            ProviderCapability.ASYNC_CANCEL,
        }
    )


def test_narrow_contract_matrix_needs_no_unsupported_stubs() -> None:
    compute = ComputeFake()
    inference = InferenceFake()
    async_inference = AsyncFake()
    dual = DualFake()

    assert isinstance(compute, ComputeProvider)
    assert not isinstance(compute, InferenceProvider)
    assert isinstance(inference, InferenceProvider)
    assert not isinstance(inference, ComputeProvider)
    assert isinstance(async_inference, AsyncInferenceProvider)
    assert isinstance(async_inference, AsyncInferenceStatusProvider)
    assert isinstance(async_inference, AsyncInferenceCancelProvider)
    assert not isinstance(async_inference, ComputeProvider)
    assert not isinstance(async_inference, InferenceProvider)
    assert isinstance(dual, ComputeProvider)
    assert isinstance(dual, InferenceProvider)
    assert isinstance(dual, AsyncInferenceProvider)
    assert isinstance(dual, AsyncInferenceStatusProvider)
    assert isinstance(dual, AsyncInferenceCancelProvider)
    assert not hasattr(inference, "provision")
    assert not hasattr(compute, "infer")
    assert not hasattr(inference, "submit")
    assert not hasattr(async_inference, "infer")


def test_capability_lookup_is_deterministic_and_typed() -> None:
    registry = ProviderRegistry()
    registry.register(ComputeFake())
    registry.register(InferenceFake())
    registry.register(AsyncFake())
    registry.register(DualFake())

    assert registry.ids_for_capability(ProviderCapability.COMPUTE) == ("fake", "dual_fake")
    assert registry.ids_for_capability(ProviderCapability.SYNC_INFERENCE) == (
        "inference_fake",
        "dual_fake",
    )
    assert registry.ids_for_capability(ProviderCapability.ASYNC_INFERENCE) == (
        "async_fake",
        "dual_fake",
    )
    assert isinstance(registry.lookup_compute("fake"), ComputeProvider)
    assert isinstance(registry.lookup_inference("inference_fake"), InferenceProvider)
    assert isinstance(registry.lookup_async_inference("async_fake"), AsyncInferenceProvider)
    assert isinstance(registry.lookup_async_status("async_fake"), AsyncInferenceStatusProvider)
    assert isinstance(registry.lookup_async_cancel("async_fake"), AsyncInferenceCancelProvider)
    with pytest.raises(UnsupportedProviderCapabilityError, match="sync_inference"):
        registry.lookup_inference("fake")


def test_registry_rejects_contract_metadata_mismatch() -> None:
    class InvalidCompute(ComputeFake):
        capabilities = frozenset({ProviderCapability.SYNC_INFERENCE})

    with pytest.raises(InvalidProviderRegistrationError, match="compute capability"):
        ProviderRegistry().register(InvalidCompute())

    class InvalidAsyncStatus(FakeMetadata):
        id = "invalid_async_status"
        capabilities = frozenset({ProviderCapability.ASYNC_STATUS})

    with pytest.raises(InvalidProviderRegistrationError, match="async status capability"):
        ProviderRegistry().register(InvalidAsyncStatus())


def test_async_capabilities_validate_independently_without_operation_stubs() -> None:
    registry = ProviderRegistry()
    adapter = SubmitOnlyFake()

    registry.register(adapter)

    assert registry.lookup_async_inference(adapter.id) is adapter
    assert not hasattr(adapter, "job_status")
    assert not hasattr(adapter, "cancel_job")
    with pytest.raises(UnsupportedProviderCapabilityError, match="async_status"):
        registry.lookup_async_status(adapter.id)


def test_default_registry_capabilities_are_stable_and_current() -> None:
    registry = create_default_registry()

    assert registry.ids == (
        "runpod",
        "vast",
        "together",
        "lambda_cloud",
        "openai_gateway",
        "model_studio",
    )
    assert registry.ids_for_capability(ProviderCapability.COMPUTE) == (
        "runpod",
        "vast",
        "lambda_cloud",
    )
    assert registry.ids_for_capability(ProviderCapability.SYNC_INFERENCE) == (
        "runpod",
        "together",
        "openai_gateway",
        "model_studio",
    )
    assert registry.ids_for_capability(ProviderCapability.ASYNC_INFERENCE) == ("runpod",)
    assert isinstance(registry.lookup_async_inference("runpod"), AsyncInferenceProvider)
    assert isinstance(registry.lookup_async_status("runpod"), AsyncInferenceStatusProvider)
    assert isinstance(registry.lookup_async_cancel("runpod"), AsyncInferenceCancelProvider)
    assert registry.ids_for_capability(ProviderCapability.ACTUAL_COST) == ("runpod",)
    assert registry.ids_for_capability(ProviderCapability.AVAILABILITY) == (
        "vast",
        "together",
        "lambda_cloud",
        "openai_gateway",
        "model_studio",
    )


def test_registry_rejects_secret_bearing_serialized_config_safely() -> None:
    registry = create_default_registry()
    secret = "provider-secret-canary"  # pragma: allowlist secret

    with pytest.raises(ValueError) as raised:
        registry.validate_serialized_config("runpod", {"nested": {"api_key": secret}})

    assert secret not in str(raised.value)
    registry.validate_serialized_config(
        "runpod",
        {
            "api_key_env": "RUNPOD_API_KEY",  # pragma: allowlist secret
            "clientSecretEnv": "RUNPOD_CLIENT_SECRET",  # pragma: allowlist secret
            "image_ref": "example/model-server:test",
            "imageRef": "example/model-server:test",
        },
    )


def test_credential_reference_repr_contains_only_the_name() -> None:
    reference = CredentialReference("RUNPOD_API_KEY")

    assert repr(reference) == "CredentialReference(name='RUNPOD_API_KEY')"
