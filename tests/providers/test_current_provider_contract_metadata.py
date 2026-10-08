"""Hermetic checks that operator-contract metadata matches adapter constants."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from pitwall.providers.lambda_cloud import (
    LAMBDA_CLOUD_API_URL,
    LAMBDA_CLOUD_LAUNCH_INTERVAL_S,
    LAMBDA_CLOUD_REQUEST_INTERVAL_S,
)
from pitwall.providers.together import _DEFAULT_BASE_URL
from pitwall.providers.vast import (
    _VAST_INSTANCE_PAGE_LIMIT,
    VAST_API_URL,
    VAST_INSTANCES_API_URL,
)

_FIXTURE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "providers"
    / "current_provider_contracts_2026-09-01.json"
)


def test_current_provider_contract_fixture_is_safe_and_matches_adapter_boundaries() -> None:
    payload = json.loads(_FIXTURE.read_text())
    metadata = payload["metadata"]
    providers = payload["providers"]

    assert metadata["captured_at_utc"] == "2026-09-01T00:00:00Z"
    assert metadata["evidence_kind"] == "official-provider-contract-reference"
    assert metadata["hash_algorithm"] == "sha256"
    assert metadata["hash_normalization"] == (
        "LF-joined endpoint/auth/version facts; not a downloaded-document hash"
    )
    assert "credential" in metadata["purpose"].lower()

    vast = providers["vast"]
    assert vast["auth"] == "bearer"
    assert vast["marketplace_base_url"] == VAST_API_URL
    assert vast["instance_base_url"] == VAST_INSTANCES_API_URL
    assert vast["instance_pagination"] == {
        "cursor_parameter": "after_token",
        "maximum_page_size": _VAST_INSTANCE_PAGE_LIMIT,
        "maximum_items": 100,
    }
    assert vast["source_contract"] == "vast-api-v0-bundles-2026-09-01"
    assert "".join(vast["normalized_contract_sha256_hex_groups"]) == _hash(
        *vast["official_references"],
        vast["auth"],
        vast["marketplace_base_url"],
        vast["instance_base_url"],
        vast["instance_pagination"]["cursor_parameter"],
        vast["instance_pagination"]["maximum_page_size"],
        vast["instance_pagination"]["maximum_items"],
        vast["source_contract"],
    )

    together = providers["together"]
    assert together["auth"] == "bearer"
    assert together["base_url"] == _DEFAULT_BASE_URL
    assert together["availability_path"] == "/models"
    assert together["source_contract"] == "together-v1-models-2026-09-01"
    assert "".join(together["normalized_contract_sha256_hex_groups"]) == _hash(
        *together["official_references"],
        together["auth"],
        together["base_url"],
        together["availability_path"],
        together["source_contract"],
    )

    lambda_cloud = providers["lambda_cloud"]
    assert lambda_cloud["auth"] == "bearer"
    assert lambda_cloud["base_url"] == LAMBDA_CLOUD_API_URL
    assert lambda_cloud["availability_path"] == "/instance-types"
    assert lambda_cloud["openapi_version"] == "1.10.0"
    assert lambda_cloud["minimum_request_interval_seconds"] == LAMBDA_CLOUD_REQUEST_INTERVAL_S
    assert lambda_cloud["minimum_launch_interval_seconds"] == LAMBDA_CLOUD_LAUNCH_INTERVAL_S
    assert lambda_cloud["source_contract"] == "lambda-cloud-openapi-1.10.0"
    assert "".join(lambda_cloud["normalized_contract_sha256_hex_groups"]) == _hash(
        *lambda_cloud["official_references"],
        lambda_cloud["auth"],
        lambda_cloud["base_url"],
        lambda_cloud["availability_path"],
        lambda_cloud["openapi_version"],
        lambda_cloud["minimum_request_interval_seconds"],
        lambda_cloud["minimum_launch_interval_seconds"],
        lambda_cloud["source_contract"],
    )

    serialized = json.dumps(payload, sort_keys=True).lower()
    for forbidden in ("api_key=", "bearer ", "secret_value", "token_value"):
        assert forbidden not in serialized


def _hash(*values: object) -> str:
    return sha256("\n".join(str(value) for value in values).encode()).hexdigest()
