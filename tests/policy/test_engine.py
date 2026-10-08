"""Hermetic tests for the Policy-as-Code evaluator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from pitwall.audit._runtime_config import RuntimeAuditConfig
from pitwall.policy import (
    Policy,
    PolicyCondition,
    PolicyRule,
    PolicySet,
    PolicyTarget,
    evaluate_default_policies,
    evaluate_policies,
    load_policy_file,
)


def _provider(
    provider_id: str,
    *,
    provider_type: str = "pod_lease",
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": provider_id,
        "provider_type": provider_type,
        "config": config or {},
    }


class _AuditConfig:
    def __init__(
        self,
        *,
        providers: list[dict[str, Any]] | None = None,
        workloads: list[dict[str, Any]] | None = None,
    ) -> None:
        self._providers = providers or []
        self._workloads = workloads or []

    def provider_fixtures(self) -> list[dict[str, Any]]:
        return self._providers

    def workloads(self) -> list[dict[str, Any]]:
        return self._workloads


def test_policy_schema_rejects_policy_without_rules() -> None:
    with pytest.raises(ValidationError):
        Policy(
            id="policy.empty",
            target=PolicyTarget.PROVIDER,
            rules=[],
        )


def test_evaluator_denies_matching_provider_violation() -> None:
    policies = PolicySet(
        policies=[
            Policy(
                id="provider.no-static-r2-env",
                target=PolicyTarget.PROVIDER,
                description="Pod lease providers must not inject long-lived R2 credentials.",
                when=[
                    PolicyCondition(
                        path="provider_type",
                        operator="equals",
                        value="pod_lease",
                    )
                ],
                rules=[
                    PolicyRule(
                        path="config.env_vars",
                        operator="contains_none",
                        value=["R2_ACCESS_KEY", "AWS_SECRET_ACCESS_KEY"],
                        message="pod lease provider injects static R2 credentials",
                    )
                ],
            )
        ]
    )
    cfg = _AuditConfig(
        providers=[
            _provider(
                "prov_bad",
                config={"env_vars": {"R2_ACCESS_KEY": "raw-secret"}},
            )
        ]
    )

    result = evaluate_policies(policies, cfg)

    assert result.allowed is False
    assert result.decision == "deny"
    assert len(result.violations) == 1
    violation = result.violations[0]
    assert violation.policy_id == "provider.no-static-r2-env"
    assert violation.target == PolicyTarget.PROVIDER
    assert violation.target_id == "prov_bad"
    assert violation.path == "config.env_vars"
    assert violation.message == "pod lease provider injects static R2 credentials"
    assert "raw-secret" not in json.dumps(result.model_dump(mode="json"))


def test_evaluator_allows_when_selector_does_not_match() -> None:
    policies = PolicySet(
        policies=[
            Policy(
                id="provider.pod-lease-readiness",
                target=PolicyTarget.PROVIDER,
                when=[
                    PolicyCondition(
                        path="provider_type",
                        operator="equals",
                        value="pod_lease",
                    )
                ],
                rules=[
                    PolicyRule(
                        path="config.readiness.required_signals",
                        operator="contains_all",
                        value=["runtime", "port_mappings", "probe_2xx"],
                    )
                ],
            )
        ]
    )
    cfg = _AuditConfig(
        providers=[
            _provider(
                "prov_lb",
                provider_type="serverless_lb",
                config={},
            )
        ]
    )

    result = evaluate_policies(policies, cfg)

    assert result.allowed is True
    assert result.decision == "allow"
    assert result.violations == ()


def test_evaluator_reports_violations_in_policy_and_target_order() -> None:
    policies = PolicySet(
        policies=[
            Policy(
                id="workload.vllm-disk",
                target=PolicyTarget.WORKLOAD,
                when=[
                    PolicyCondition(
                        path="name",
                        operator="equals",
                        value="vllm",
                    )
                ],
                rules=[
                    PolicyRule(
                        path="disk_gb",
                        operator="gte",
                        value=80,
                    )
                ],
            ),
            Policy(
                id="provider.pod-lease-readiness",
                target=PolicyTarget.PROVIDER,
                when=[
                    PolicyCondition(
                        path="provider_type",
                        operator="equals",
                        value="pod_lease",
                    )
                ],
                rules=[
                    PolicyRule(
                        path="config.readiness.required_signals",
                        operator="contains_all",
                        value=["runtime", "port_mappings", "probe_2xx"],
                    )
                ],
            ),
        ]
    )
    cfg = _AuditConfig(
        providers=[
            _provider(
                "prov_a",
                config={"readiness": {"required_signals": ["runtime"]}},
            ),
            _provider(
                "prov_b",
                config={"readiness": {"required_signals": ["runtime", "probe_2xx"]}},
            ),
        ],
        workloads=[
            {"name": "vllm", "disk_gb": 40},
            {"name": "vllm", "disk_gb": 20},
        ],
    )

    result = evaluate_policies(policies, cfg)

    assert [
        (violation.policy_id, violation.target_id, violation.path)
        for violation in result.violations
    ] == [
        ("workload.vllm-disk", "workload[0]", "disk_gb"),
        ("workload.vllm-disk", "workload[1]", "disk_gb"),
        ("provider.pod-lease-readiness", "prov_a", "config.readiness.required_signals"),
        ("provider.pod-lease-readiness", "prov_b", "config.readiness.required_signals"),
    ]


def test_load_policy_file_accepts_yaml_subset(tmp_path: Path) -> None:
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text(
        """
version: 1
policies:
  - id: provider.no-community-cloud-volume
    target: provider
    description: Volume-backed providers must stay on secure cloud.
    when:
      - path: config.volume_id
        operator: exists
    rules:
      - path: cloud_type
        operator: not_equals
        value: COMMUNITY
        message: volume-backed providers cannot run on community cloud
""".lstrip(),
        encoding="utf-8",
    )

    loaded = load_policy_file(policy_file)

    assert loaded.version == 1
    assert loaded.policies[0].id == "provider.no-community-cloud-volume"
    assert loaded.policies[0].rules[0].message == (
        "volume-backed providers cannot run on community cloud"
    )


def test_default_policy_examples_allow_runtime_audit_config() -> None:
    result = evaluate_default_policies(RuntimeAuditConfig())

    assert result.allowed is True
    assert result.decision == "allow"
    assert result.violations == ()


def _evidence_policy(path: str, value: Any) -> PolicySet:
    return PolicySet(
        policies=[
            Policy(
                id="provider.evidence",
                target=PolicyTarget.PROVIDER,
                rules=[PolicyRule(path=path, operator="equals", value=value)],
            )
        ]
    )


def _violation_actual(path: str, config: dict[str, Any]) -> Any:
    result = evaluate_policies(
        _evidence_policy(path, "never-equal"),
        _AuditConfig(providers=[_provider("prov_x", config=config)]),
    )
    assert len(result.violations) == 1
    return result.violations[0].actual


def test_violation_evidence_redacts_camel_case_credential_keys() -> None:
    actual = _violation_actual(
        "config",
        {
            "apiKey": "raw-api",
            "clientSecret": "raw-client",
            "auth": {"accessToken": ["raw-token"]},
            "accessKey": "raw-access",
            "x-API-Key": "raw-header",
            "region": "eu-1",
        },
    )

    assert actual == {
        "apiKey": "[REDACTED]",
        "clientSecret": "[REDACTED]",
        "auth": {"accessToken": "[REDACTED]"},
        "accessKey": "[REDACTED]",
        "x-API-Key": "[REDACTED]",
        "region": "eu-1",
    }


@pytest.mark.parametrize(
    "field",
    ["apiKey", "api_key", "API-KEY", "clientSecret", "accessKey", "auth.accessToken"],
)
@pytest.mark.parametrize("secret", ["raw-secret", ["raw-secret"], {"nested": "raw-secret"}])
def test_violation_evidence_redacts_a_value_of_any_shape_at_a_credential_path(
    field: str, secret: Any
) -> None:
    config: dict[str, Any] = {}
    cursor = config
    *parents, leaf = field.split(".")
    for parent in parents:
        cursor = cursor.setdefault(parent, {})
    cursor[leaf] = secret

    actual = _violation_actual(f"config.{field}", config)

    assert actual == "[REDACTED]"


@pytest.mark.parametrize("field", ["max_tokens", "maxTokens", "total_tokens", "config_tokens"])
@pytest.mark.parametrize("number", [100, 1.5, True, None])
def test_violation_evidence_keeps_numbers_and_flags_under_a_token_count_key(
    field: str, number: Any
) -> None:
    assert _violation_actual(f"config.{field}", {field: number}) == number
    assert _violation_actual("config", {field: number, "apiKey": "x"}) == {
        "apiKey": "[REDACTED]",
        field: number,
    }


@pytest.mark.parametrize(
    "field",
    ["apikey", "APIKEY", "accesskey", "AccessKey", "secretkey", "authtoken", "clientsecret"],
)
def test_violation_evidence_redacts_credential_keys_written_without_separators(
    field: str,
) -> None:
    assert _violation_actual(f"config.{field}", {field: "raw-secret"}) == "[REDACTED]"
    assert _violation_actual("config", {field: "raw-secret", "region": "eu-1"}) == {
        field: "[REDACTED]",
        "region": "eu-1",
    }
