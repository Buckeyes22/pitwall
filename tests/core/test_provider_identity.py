from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from pitwall.api.provider_schemas import ProviderCreate
from pitwall.core import models
from pitwall.core.enums import LeaseState, ProviderAdapterId, ProviderType, WorkloadState
from pitwall.core.models import (
    ConfigAuditEntry,
    Lease,
    Provider,
    Workload,
    redact_provider_serialized_config,
)

_NOW = dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.UTC)
_SECRET_KEY_STYLE_CANARY = "camel-secret-canary"  # pragma: allowlist secret


def test_provider_config_key_normalization_is_linear_and_preserves_boundaries() -> None:
    assert models._normalize_provider_config_key("APIKey.Value-name") == "api_key_value_name"

    repeated_acronym = "A" * 100_000 + "a"
    normalized = models._normalize_provider_config_key(repeated_acronym)

    assert normalized == "a" * 99_999 + "_aa"


def test_provider_adapter_defaults_preserve_old_runpod_clients() -> None:
    provider = Provider(
        id="prov-old",
        capability_id="cap-old",
        name="old",
        provider_type=ProviderType.SERVERLESS_QUEUE,
        priority=0,
        updated_at=_NOW,
    )

    assert provider.adapter_id is ProviderAdapterId.RUNPOD
    assert provider.credential_ref == "RUNPOD_API_KEY"


def test_non_runpod_adapter_gets_its_conventional_reference() -> None:
    provider = Provider(
        id="prov-vast",
        capability_id="cap-gpu",
        name="vast",
        adapter_id=ProviderAdapterId.VAST,
        provider_type=ProviderType.POD_LEASE,
        priority=0,
        updated_at=_NOW,
    )

    assert provider.credential_ref == "VAST_API_KEY"


def test_legacy_lease_id_is_normalized_to_generic_id() -> None:
    lease = Lease(
        id="lease-old",
        provider_id="prov-old",
        runpod_pod_id="pod-1",
        state=LeaseState.CREATING,
        created_at=_NOW,
        expires_at=_NOW + dt.timedelta(hours=1),
        renewal_policy="manual",
    )

    assert lease.external_resource_id == "pod-1"
    assert lease.runpod_pod_id == "pod-1"


def test_non_runpod_lease_uses_only_generic_id() -> None:
    lease = Lease(
        id="lease-vast",
        provider_id="prov-vast",
        external_resource_id="vast-1",
        state=LeaseState.CREATING,
        created_at=_NOW,
        expires_at=_NOW + dt.timedelta(hours=1),
        renewal_policy="manual",
    )

    assert lease.external_resource_id == "vast-1"
    assert lease.runpod_pod_id is None


def test_lease_rejects_disagreeing_compatibility_ids() -> None:
    with pytest.raises(ValidationError, match="must match"):
        Lease(
            id="lease-bad",
            provider_id="prov-runpod",
            external_resource_id="pod-new",
            runpod_pod_id="pod-old",
            state=LeaseState.CREATING,
            created_at=_NOW,
            expires_at=_NOW + dt.timedelta(hours=1),
            renewal_policy="manual",
        )


def test_workload_legacy_id_is_normalized_and_mismatch_is_rejected() -> None:
    workload = Workload(
        id="wkl-old",
        capability_id="cap",
        provider_id="prov",
        type="async_job",
        state=WorkloadState.QUEUED,
        runpod_job_id="job-1",
        submitted_at=_NOW,
    )

    assert workload.external_job_id == "job-1"
    with pytest.raises(ValidationError, match="must match"):
        Workload(
            id="wkl-bad",
            capability_id="cap",
            provider_id="prov",
            type="async_job",
            state=WorkloadState.QUEUED,
            external_job_id="job-new",
            runpod_job_id="job-old",
            submitted_at=_NOW,
        )


def test_provider_crud_schema_rejects_unknown_adapter_and_redacts_raw_secret() -> None:
    with pytest.raises(ValidationError, match="adapter_id"):
        ProviderCreate(
            capability_id="cap",
            name="unknown",
            adapter_id="unknown",
            provider_type=ProviderType.POD_LEASE,
        )

    secret = "crud-secret-canary"  # pragma: allowlist secret
    payload = {
        "capability_id": "cap",
        "name": "unsafe",
        "provider_type": "pod_lease",
        "config": {"nested": {"api_key": secret}},
    }
    with pytest.raises(ValidationError) as raised:
        ProviderCreate.model_validate(payload)

    assert secret not in str(raised.value)
    assert secret not in str(raised.value.errors())


@pytest.mark.parametrize(
    "config",
    [
        {"apiKey": _SECRET_KEY_STYLE_CANARY},
        {"nested": {"clientSecret": _SECRET_KEY_STYLE_CANARY}},
        {"accessKey": _SECRET_KEY_STYLE_CANARY},
        {"privateKey": _SECRET_KEY_STYLE_CANARY},
        {"items": [[{"access.key": _SECRET_KEY_STYLE_CANARY}]]},
        {"private key": _SECRET_KEY_STYLE_CANARY},
    ],
)
def test_provider_crud_rejects_realistic_secret_key_styles_without_echo(
    config: dict[str, object],
) -> None:
    secret = _SECRET_KEY_STYLE_CANARY

    with pytest.raises(ValidationError) as raised:
        ProviderCreate(
            capability_id="cap",
            name="unsafe-camel",
            provider_type=ProviderType.POD_LEASE,
            config=config,
        )

    assert secret not in str(raised.value)
    assert secret not in str(raised.value.errors())
    assert secret not in str(redact_provider_serialized_config(config))


def test_provider_model_dump_and_repr_redact_legacy_credential_values() -> None:
    secret = "legacy-provider-secret-canary"  # pragma: allowlist secret
    provider = Provider(
        id="prov-legacy",
        capability_id="cap",
        name="legacy",
        provider_type=ProviderType.POD_LEASE,
        config={"nested": {"api_key": secret}},
        priority=0,
        updated_at=_NOW,
    )

    assert secret not in repr(provider)
    assert secret not in str(provider.model_dump(mode="json"))
    assert provider.model_dump(mode="json")["config"] == {"nested": {"api_key": "[REDACTED]"}}


def test_provider_and_audit_serialization_redact_deep_camel_case_values() -> None:
    secret = "deep-camel-secret-canary"  # pragma: allowlist secret
    config = {
        "imageRef": "example/model:test",
        "items": [[{"clientSecret": secret, "label": "retained"}]],
    }
    provider = Provider(
        id="prov-camel-legacy",
        capability_id="cap",
        name="camel-legacy",
        provider_type=ProviderType.POD_LEASE,
        config=config,
        priority=0,
        updated_at=_NOW,
    )
    audit = ConfigAuditEntry(
        id=1,
        actor="system",
        action="update",
        entity_type="provider",
        entity_id=provider.id,
        old_value={"config": config},
        new_value={"config": {"apiKey": secret}},
        created_at=_NOW,
    )

    assert provider.model_dump(mode="json")["config"] == {
        "imageRef": "example/model:test",
        "items": [[{"clientSecret": "[REDACTED]", "label": "retained"}]],
    }
    assert audit.old_value == {
        "config": {
            "imageRef": "example/model:test",
            "items": [[{"clientSecret": "[REDACTED]", "label": "retained"}]],
        }
    }
    assert audit.new_value == {"config": {"apiKey": "[REDACTED]"}}
    assert secret not in repr(provider)
    assert secret not in str(audit.model_dump(mode="json"))


def test_invalid_reference_shaped_config_is_redacted_from_validation_errors() -> None:
    secret = "sk-secret-reference-canary"  # pragma: allowlist secret
    payload = {
        "capability_id": "cap",
        "name": "unsafe-ref",
        "provider_type": "pod_lease",
        "config": {"api_key_env": secret},
    }

    with pytest.raises(ValidationError) as raised:
        ProviderCreate.model_validate(payload)

    assert secret not in str(raised.value)
    assert secret not in str(raised.value.errors())


def test_invalid_persisted_credential_reference_is_redacted_from_errors() -> None:
    secret = "sk-credential-ref-canary"  # pragma: allowlist secret

    with pytest.raises(ValidationError) as raised:
        Provider(
            id="prov-unsafe-ref",
            capability_id="cap",
            name="unsafe-ref",
            credential_ref=secret,
            provider_type=ProviderType.POD_LEASE,
            priority=0,
            updated_at=_NOW,
        )

    assert secret not in str(raised.value)
    assert secret not in str(raised.value.errors())
