"""Provider plugin registry and credential validation."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, cast

from pydantic import BaseModel, ValidationError

from pitwall.core.models import validate_provider_serialized_config
from pitwall.providers.interface import (
    ActualCostProvider,
    AsyncInferenceCancelProvider,
    AsyncInferenceProvider,
    AsyncInferenceStatusProvider,
    AvailabilityProvider,
    ComputeProvider,
    InferenceProvider,
    ProviderAdapter,
    ProviderCapability,
    ProviderDeclaration,
)


class ProviderRegistryError(RuntimeError):
    """Base class for provider registry errors."""


class DuplicateProviderError(ProviderRegistryError):
    """Raised when a provider id is registered more than once."""

    def __init__(self, provider_id: str) -> None:
        super().__init__(f"provider {provider_id!r} is already registered")
        self.provider_id = provider_id


class ProviderNotRegisteredError(ProviderRegistryError):
    """Raised when a provider id is not present in the registry."""

    def __init__(self, provider_id: str) -> None:
        super().__init__(f"provider {provider_id!r} is not registered")
        self.provider_id = provider_id


class UnsupportedProviderCapabilityError(ProviderRegistryError):
    """Raised when a registered adapter does not declare an operation."""

    def __init__(self, provider_id: str, capability: ProviderCapability) -> None:
        super().__init__(
            f"provider {provider_id!r} does not support capability {capability.value!r}"
        )
        self.provider_id = provider_id
        self.capability = capability


class InvalidProviderRegistrationError(ProviderRegistryError):
    """Raised when adapter metadata disagrees with its narrow contract."""


class CredentialValidationError(ProviderRegistryError):
    """Safe-to-log credential validation failure."""

    def __init__(
        self,
        provider_id: str,
        fields: Iterable[str],
    ) -> None:
        normalized_fields = tuple(dict.fromkeys(fields))
        field_list = ", ".join(normalized_fields) if normalized_fields else "<model>"
        super().__init__(
            f"credentials for provider {provider_id!r} failed validation for fields: {field_list}"
        )
        self.provider_id = provider_id
        self.fields = normalized_fields


class ProviderRegistry:
    """In-memory registry of provider plugins keyed by stable provider id."""

    def __init__(self) -> None:
        self._providers: dict[str, ProviderAdapter] = {}

    @property
    def ids(self) -> tuple[str, ...]:
        """Registered provider ids in registration order."""

        return tuple(self._providers)

    def register(
        self,
        provider: ProviderAdapter,
        *,
        replace: bool = False,
    ) -> ProviderAdapter:
        """Register *provider* and return it."""

        provider_id = _validated_provider_id(provider.id)
        _validated_credential_schema(provider.credential_schema)
        _validated_capabilities(provider)
        declaration = _validated_declaration(provider)
        if provider_id in self._providers and not replace:
            raise DuplicateProviderError(provider_id)
        for other_id, other in self._providers.items():
            clash = declaration.provider_types & other.declaration.provider_types
            if other_id != provider_id and clash:
                raise InvalidProviderRegistrationError(
                    f"provider {provider_id!r} claims provider types already owned by "
                    f"{other_id!r}: {', '.join(sorted(clash))}"
                )
        self._providers[provider_id] = provider
        return provider

    def lookup(self, provider_id: str) -> ProviderAdapter:
        """Return the provider registered as *provider_id*."""

        try:
            return self._providers[provider_id]
        except KeyError as exc:
            raise ProviderNotRegisteredError(provider_id) from exc

    def declaration_for_adapter(self, adapter_id: str) -> ProviderDeclaration | None:
        """Return the declaration of a registered adapter, or None when it is unknown."""

        adapter = self._providers.get(adapter_id)
        return None if adapter is None else adapter.declaration

    def declaration_for_type(self, provider_type: str | None) -> ProviderDeclaration | None:
        """Return the declaration of the adapter that owns *provider_type*.

        ``None`` selects the adapter that declares itself the default for untyped rows.
        """

        for adapter in self._providers.values():
            declaration = adapter.declaration
            if provider_type is None:
                if declaration.default_for_untyped:
                    return declaration
            elif provider_type in declaration.provider_types:
                return declaration
        return None

    def declarations(self) -> tuple[tuple[str, ProviderDeclaration], ...]:
        """Return ``(adapter id, declaration)`` pairs in registration order."""

        return tuple(
            (adapter_id, adapter.declaration) for adapter_id, adapter in self._providers.items()
        )

    def supports(self, provider_id: str, capability: ProviderCapability) -> bool:
        """Return whether one adapter declares *capability*."""

        return capability in self.lookup(provider_id).capabilities

    def ids_for_capability(self, capability: ProviderCapability) -> tuple[str, ...]:
        """Return matching ids in deterministic registration order."""

        return tuple(
            provider_id
            for provider_id, provider in self._providers.items()
            if capability in provider.capabilities
        )

    def lookup_for_capability(
        self,
        provider_id: str,
        capability: ProviderCapability,
    ) -> ProviderAdapter:
        """Return an adapter only when it declares *capability*."""

        provider = self.lookup(provider_id)
        if capability not in provider.capabilities:
            raise UnsupportedProviderCapabilityError(provider_id, capability)
        return provider

    def lookup_compute(self, provider_id: str) -> ComputeProvider:
        """Return the registered compute adapter."""

        provider = self.lookup_for_capability(provider_id, ProviderCapability.COMPUTE)
        return cast(ComputeProvider, provider)

    def lookup_inference(self, provider_id: str) -> InferenceProvider:
        """Return the registered synchronous inference adapter."""

        provider = self.lookup_for_capability(
            provider_id,
            ProviderCapability.SYNC_INFERENCE,
        )
        return cast(InferenceProvider, provider)

    def lookup_async_inference(self, provider_id: str) -> AsyncInferenceProvider:
        """Return the registered asynchronous inference submit adapter."""

        provider = self.lookup_for_capability(
            provider_id,
            ProviderCapability.ASYNC_INFERENCE,
        )
        return cast(AsyncInferenceProvider, provider)

    def lookup_async_status(self, provider_id: str) -> AsyncInferenceStatusProvider:
        """Return the registered asynchronous inference status adapter."""

        provider = self.lookup_for_capability(
            provider_id,
            ProviderCapability.ASYNC_STATUS,
        )
        return cast(AsyncInferenceStatusProvider, provider)

    def lookup_async_cancel(self, provider_id: str) -> AsyncInferenceCancelProvider:
        """Return the registered asynchronous inference cancellation adapter."""

        provider = self.lookup_for_capability(
            provider_id,
            ProviderCapability.ASYNC_CANCEL,
        )
        return cast(AsyncInferenceCancelProvider, provider)

    def lookup_availability(self, provider_id: str) -> AvailabilityProvider:
        """Return the registered read-only availability adapter."""

        provider = self.lookup_for_capability(provider_id, ProviderCapability.AVAILABILITY)
        return cast(AvailabilityProvider, provider)

    def lookup_actual_cost(self, provider_id: str) -> ActualCostProvider:
        """Return the registered provider-actual billing adapter."""

        provider = self.lookup_for_capability(provider_id, ProviderCapability.ACTUAL_COST)
        return cast(ActualCostProvider, provider)

    def validate_credentials(self, provider_id: str, credentials: object) -> BaseModel:
        """Validate *credentials* against the provider's declared schema."""

        provider = self.lookup(provider_id)
        schema = provider.credential_schema
        try:
            return schema.model_validate(credentials)
        except ValidationError as exc:
            raise CredentialValidationError(
                provider_id,
                _validation_error_fields(exc),
            ) from None

    def credential_json_schema(self, provider_id: str) -> dict[str, Any]:
        """Return the provider credential JSON schema."""

        return dict(self.lookup(provider_id).credential_schema.model_json_schema())

    def validate_serialized_config(
        self,
        provider_id: str,
        config: dict[str, Any],
    ) -> None:
        """Validate adapter identity and reject serialized credential values."""

        self.lookup(provider_id)
        validate_provider_serialized_config(config)


def create_default_registry() -> ProviderRegistry:
    """Return a new registry with built-in provider plugins registered."""

    from pitwall.providers.gateway import GatewayProvider
    from pitwall.providers.lambda_cloud import LambdaCloudProvider
    from pitwall.providers.model_studio import ModelStudioProvider
    from pitwall.providers.runpod import RunPodProvider
    from pitwall.providers.together import TogetherProvider
    from pitwall.providers.vast import VastProvider

    registry = ProviderRegistry()
    registry.register(RunPodProvider())
    registry.register(VastProvider())
    registry.register(TogetherProvider())
    registry.register(LambdaCloudProvider())
    registry.register(GatewayProvider())
    registry.register(ModelStudioProvider())
    return registry


_DEFAULT_REGISTRY: ProviderRegistry | None = None


def get_default_registry() -> ProviderRegistry:
    """Return the process-wide provider registry."""

    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = create_default_registry()
    return _DEFAULT_REGISTRY


def _field(provider: object, name: str) -> str | None:
    value = provider.get(name) if isinstance(provider, Mapping) else getattr(provider, name, None)
    return None if value is None else str(value) or None


def declaration_for_provider(provider: object) -> ProviderDeclaration | None:
    """Return the declaration governing one provider record, model or plain mapping.

    The adapter that owns the record's provider type governs it (a Vast pod lease behaves like
    any pod lease); a type nobody owns falls back to the adapter the record names.
    """

    registry = get_default_registry()
    provider_type = _field(provider, "provider_type")
    if provider_type is not None:
        owner = registry.declaration_for_type(provider_type)
        if owner is not None:
            return owner
    adapter_id = _field(provider, "adapter_id")
    if adapter_id is not None:
        return registry.declaration_for_adapter(adapter_id)
    return registry.declaration_for_type(None) if provider_type is None else None


def declarations_for_provider(provider: object) -> tuple[ProviderDeclaration, ...]:
    """Return the declaration governing *provider*, or every declaration for a bare record.

    A record that names neither an adapter nor a provider type cannot be attributed to one
    adapter, so callers that only read optional hints try each declaration in turn.
    """

    if _field(provider, "adapter_id") is None and _field(provider, "provider_type") is None:
        return tuple(declaration for _, declaration in get_default_registry().declarations())
    declaration = declaration_for_provider(provider)
    return () if declaration is None else (declaration,)


def declaration_for_provider_type(provider_type: str | None) -> ProviderDeclaration | None:
    """Return the declaration of the adapter that owns *provider_type*."""

    return get_default_registry().declaration_for_type(provider_type)


def _validated_provider_id(provider_id: str) -> str:
    if not isinstance(provider_id, str) or not provider_id.strip():
        raise ValueError("provider id must be a non-empty string")
    if provider_id != provider_id.strip():
        raise ValueError("provider id must not include surrounding whitespace")
    return provider_id


def _validated_credential_schema(schema: type[BaseModel]) -> type[BaseModel]:
    if not isinstance(schema, type) or not issubclass(schema, BaseModel):
        raise ValueError("provider credential_schema must be a Pydantic BaseModel type")
    return schema


def _validated_capabilities(provider: ProviderAdapter) -> None:
    capabilities = getattr(provider, "capabilities", None)
    if not isinstance(capabilities, frozenset) or not all(
        isinstance(capability, ProviderCapability) for capability in capabilities
    ):
        raise InvalidProviderRegistrationError(
            "provider capabilities must be a frozenset of ProviderCapability values"
        )

    contracts: tuple[tuple[ProviderCapability, type[Any], str], ...] = (
        (ProviderCapability.COMPUTE, ComputeProvider, "compute"),
        (ProviderCapability.SYNC_INFERENCE, InferenceProvider, "sync inference"),
        (ProviderCapability.ASYNC_INFERENCE, AsyncInferenceProvider, "async inference"),
        (ProviderCapability.ASYNC_STATUS, AsyncInferenceStatusProvider, "async status"),
        (ProviderCapability.ASYNC_CANCEL, AsyncInferenceCancelProvider, "async cancel"),
        (ProviderCapability.ACTUAL_COST, ActualCostProvider, "actual cost"),
        (ProviderCapability.AVAILABILITY, AvailabilityProvider, "availability"),
    )
    for capability, contract, label in contracts:
        has_contract = isinstance(provider, contract)
        declares_capability = capability in capabilities
        if has_contract != declares_capability:
            raise InvalidProviderRegistrationError(
                f"provider {provider.id!r} {label} capability does not match its contract"
            )


def _validated_declaration(provider: ProviderAdapter) -> ProviderDeclaration:
    declaration = getattr(provider, "declaration", None)
    if not isinstance(declaration, ProviderDeclaration):
        raise InvalidProviderRegistrationError(
            f"provider {provider.id!r} must declare a ProviderDeclaration"
        )
    return declaration


def _validation_error_fields(exc: ValidationError) -> tuple[str, ...]:
    fields: list[str] = []
    for item in exc.errors(include_input=False, include_url=False):
        loc = item.get("loc", ())
        if isinstance(loc, tuple) and loc:
            fields.append(".".join(str(part) for part in loc))
        elif isinstance(loc, str) and loc:
            fields.append(loc)
    return tuple(fields)


__all__ = [
    "CredentialValidationError",
    "DuplicateProviderError",
    "InvalidProviderRegistrationError",
    "ProviderNotRegisteredError",
    "ProviderRegistry",
    "ProviderRegistryError",
    "UnsupportedProviderCapabilityError",
    "create_default_registry",
    "declaration_for_provider",
    "declaration_for_provider_type",
    "declarations_for_provider",
    "get_default_registry",
]
