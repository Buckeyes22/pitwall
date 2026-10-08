"""Adversarial coverage for the bounded pre-spend inspection service."""

from __future__ import annotations

import base64
import datetime as dt
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pitwall.security.pre_spend import (
    PRE_SPEND_RULES,
    PreSpendDecision,
    PreSpendFindingKind,
    PreSpendInspectionLimits,
    PreSpendInspectionService,
    PreSpendPolicyMode,
    build_pre_spend_inspection_service,
    scan_pre_spend_payload,
)

pytestmark = pytest.mark.security

_SECRET = "sk-test_1234567890abcdef1234567890abcdef"
_NOW = dt.datetime(2026, 9, 1, 12, tzinfo=dt.UTC)


def _service(**kwargs: Any) -> PreSpendInspectionService:
    # Tests that do not control the clock must not depend on wall-clock load:
    # give them a generous scan budget so a busy CI runner cannot turn a
    # redaction outcome into a timeout. Tests that pass ``limits`` or
    # ``thread_time_ns`` keep exact control of the deadline.
    if "limits" not in kwargs and "thread_time_ns" not in kwargs:
        kwargs["limits"] = PreSpendInspectionLimits(timeout_ms=60_000)
    return PreSpendInspectionService(now_factory=lambda: _NOW, **kwargs)


@pytest.mark.parametrize(
    ("mode", "payload", "decision"),
    [
        ("balanced", {"input": "ada@example.com"}, PreSpendDecision.REDACT),
        ("block", {"input": "ada@example.com"}, PreSpendDecision.BLOCK),
        ("redact", {"token": _SECRET}, PreSpendDecision.REDACT),
    ],
)
def test_configured_service_modes_are_explicit(
    mode: str,
    payload: dict[str, str],
    decision: PreSpendDecision,
) -> None:
    service = build_pre_spend_inspection_service(mode=mode)

    assert service.status().mode.value == mode
    assert service.preview(payload).decision == decision


def test_configured_service_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="balanced, block, or redact"):
        build_pre_spend_inspection_service(mode="monitor")


def test_rule_catalogue_is_complete_and_contains_no_match_material() -> None:
    rules = {rule.rule_id: rule for rule in PRE_SPEND_RULES}

    assert {
        "secret_field",
        "private_key",
        "labeled_cloud_secret_assignment",
        "openai_style_token",
        "github_pat",
        "github_token",
        "gitlab_token",
        "slack_token",
        "aws_access_key_id",
        "bearer_token",
        "hugging_face_token",
        "runpod_api_token",
        "url_credentials",
        "explicit_base64_secret",
        "email",
        "us_ssn",
        "unsupported_content",
        "schema_validation",
    } == set(rules)
    serialized = json.dumps([rule.to_dict() for rule in PRE_SPEND_RULES], sort_keys=True)
    assert _SECRET not in serialized
    assert all(rule.description.endswith(".") for rule in rules.values())


def test_inspection_records_only_safe_aggregate_and_last_decision_metadata() -> None:
    service = _service()

    result = service.inspect(
        {
            "headers": {"Authorization": f"Bearer {_SECRET}"},
            "messages": [{"content": "contact ada@example.com"}],
        }
    )
    status = service.status()

    assert result.decision == PreSpendDecision.BLOCK
    assert result.redacted_payload == {
        "headers": {"Authorization": "[REDACTED:secret]"},
        "messages": [{"content": "contact [REDACTED:email]"}],
    }
    assert status.counters.to_dict() == {"total": 1, "allow": 0, "redact": 0, "block": 1}
    assert status.last_decision is not None
    assert status.last_decision.observed_at == _NOW
    assert status.last_decision.rule_ids == ("email", "secret_field")
    assert _SECRET not in repr(result)
    assert _SECRET not in repr(status)
    assert _SECRET not in json.dumps(result.semantic_dict(), sort_keys=True)


def test_preview_is_non_mutating_and_uses_the_same_typed_result() -> None:
    service = _service()

    preview = service.preview({"input": "ada@example.com"})

    assert preview.decision == PreSpendDecision.REDACT
    assert preview.redacted_payload == {"input": "[REDACTED:email]"}
    assert service.status().counters.total == 0
    assert service.status().last_decision is None


@pytest.mark.parametrize(
    ("mode", "payload", "expected"),
    [
        (PreSpendPolicyMode.BALANCED, {"input": _SECRET}, PreSpendDecision.BLOCK),
        (
            PreSpendPolicyMode.BALANCED,
            {"input": "ada@example.com"},
            PreSpendDecision.REDACT,
        ),
        (PreSpendPolicyMode.BLOCK, {"input": "ada@example.com"}, PreSpendDecision.BLOCK),
        (PreSpendPolicyMode.REDACT, {"input": _SECRET}, PreSpendDecision.REDACT),
    ],
)
def test_configured_mode_has_stable_block_and_redact_semantics(
    mode: PreSpendPolicyMode,
    payload: dict[str, str],
    expected: PreSpendDecision,
) -> None:
    result = _service(mode=mode).preview(payload)

    assert result.decision == expected
    assert _SECRET not in repr(result)


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (b"opaque binary", "opaque_content"),
        ({"input": "prefix\x00suffix"}, "null_byte"),
        # Regression (admin fuzz): a NUL in a key passed inspection and reached jsonb.
        ({"in\x00put": "value"}, "null_byte"),
        ({"outer": {"in\x00ner": 1}}, "null_byte"),
        ({"input": object()}, "opaque_content"),
        ({1: "non-string key"}, "opaque_mapping_key"),
        ({_SECRET: "value"}, "sensitive_mapping_key"),
    ],
)
def test_opaque_binary_and_unsupported_content_fail_closed_without_echo(
    payload: Any,
    reason: str,
) -> None:
    result = _service().preview(payload)

    assert result.decision == PreSpendDecision.BLOCK
    assert result.redacted_payload is None
    assert result.limited is True
    assert result.limit_reason == reason
    assert result.findings[-1].kind == PreSpendFindingKind.UNSUPPORTED


def test_input_depth_item_and_time_limits_fail_closed() -> None:
    oversize = _service(limits=PreSpendInspectionLimits(max_input_bytes=8)).preview(
        {"input": "too large"}
    )
    too_deep = _service(limits=PreSpendInspectionLimits(max_depth=2)).preview(
        {"a": {"b": {"c": "value"}}}
    )
    too_many = _service(limits=PreSpendInspectionLimits(max_items=2)).preview(["one", "two"])
    ticks = iter((0, 60_000_000, 60_000_000))
    timed_out = _service(thread_time_ns=lambda: next(ticks)).preview({"input": "value"})

    assert oversize.limit_reason == "max_input_bytes"
    assert too_deep.limit_reason == "max_depth"
    assert too_many.limit_reason == "max_items"
    assert timed_out.limit_reason == "timeout"
    assert all(
        result.decision == PreSpendDecision.BLOCK
        for result in (oversize, too_deep, too_many, timed_out)
    )


def test_explicit_bounded_base64_secret_is_detected_without_decoded_echo() -> None:
    encoded = base64.b64encode(_SECRET.encode()).decode()

    result = _service().preview({"tool": {"arguments": f"base64:{encoded}"}})

    assert result.decision == PreSpendDecision.BLOCK
    assert result.findings[0].rule == "explicit_base64_secret"
    assert result.redacted_payload == {"tool": {"arguments": "base64:[REDACTED:secret]"}}
    assert encoded not in repr(result)
    assert _SECRET not in repr(result)


@pytest.mark.parametrize(
    ("payload", "secret"),
    [
        (
            {"environment": {"HF_TOKEN": "opaque-hf-credential-value"}},
            "opaque-hf-credential-value",
        ),
        ({"headers": {"Authorization": f"Bearer {_SECRET}"}}, _SECRET),
        ({"tool_calls": [{"arguments": {"password": "tool-secret-value"}}]}, "tool-secret-value"),
        ({"credentials": {"value": "nested-secret-value"}}, "nested-secret-value"),
        ({"api_key": ["list-secret-value"]}, "list-secret-value"),
    ],
)
def test_environment_header_tool_and_secret_container_shapes_are_scanned(
    payload: dict[str, Any],
    secret: str,
) -> None:
    result = _service().preview(payload)

    assert result.decision == PreSpendDecision.BLOCK
    assert secret not in repr(result)


@pytest.mark.parametrize(
    "field_name",
    [
        "apiKey",
        "clientSecret",
        "accessKey",
        "privateKey",
        "client-secret",
        "private key",
        "nested.access-key",
    ],
)
def test_secret_field_normalization_covers_camel_case_and_realistic_separators(
    field_name: str,
) -> None:
    secret = "opaque-value-that-pattern-matching-does-not-recognize"

    result = _service().preview({"outer": [{field_name: secret}]})

    assert result.decision == PreSpendDecision.BLOCK
    assert result.findings[0].rule == "secret_field"
    assert secret not in repr(result)


def test_stream_fragments_are_independently_scanned() -> None:
    result = _service().preview({"stream_fragments": ["first", "owner@example.com", "last"]})

    assert result.decision == PreSpendDecision.REDACT
    assert result.redacted_payload == {"stream_fragments": ["first", "[REDACTED:email]", "last"]}


def test_multipart_binary_is_fail_closed() -> None:
    result = _service().preview({"multipart": {"metadata": "owner@example.com", "file": b"opaque"}})

    assert result.decision == PreSpendDecision.BLOCK
    assert result.redacted_payload is None
    assert result.limit_reason == "opaque_content"


@pytest.mark.parametrize(
    ("encoded", "reason"),
    [
        ("not-valid-base64!", "invalid_encoded_content"),
        (base64.b64encode(b"\xff\xfe\xfd binary").decode(), "encoded_binary"),
    ],
)
def test_explicit_malformed_or_binary_base64_is_fail_closed(
    encoded: str,
    reason: str,
) -> None:
    result = _service().preview({"input": f"base64:{encoded}"})

    assert result.decision == PreSpendDecision.BLOCK
    assert result.redacted_payload is None
    assert result.limit_reason == reason
    assert encoded not in repr(result)


@pytest.mark.parametrize(
    "value",
    [
        "hf_1234567890abcdef1234567890abcdef",
        "rpa_1234567890abcdef1234567890abcdef",
        "postgresql://operator:database-password@example.invalid/pitwall",
    ],
)
def test_current_product_credential_shapes_are_blocked(value: str) -> None:
    result = _service().preview({"input": value})

    assert result.decision == PreSpendDecision.BLOCK
    assert value not in repr(result)


def test_malformed_unicode_is_a_typed_block_and_never_escapes_in_exception_state() -> None:
    value = f"{_SECRET}\ud800"

    result = _service().preview({"input": value})

    assert result.decision == PreSpendDecision.BLOCK
    assert result.limit_reason == "invalid_unicode"
    assert result.redacted_payload is None
    assert value not in repr(result)


def test_unicode_and_conservative_pii_patterns_preserve_json_shape() -> None:
    payload = {
        "messages": [
            {"content": "こんにちは ada@example.com"},
            {"content": "SSN 123-45-6789; control 000-12-3456"},
        ]
    }

    result = _service().preview(payload)

    assert result.decision == PreSpendDecision.REDACT
    assert result.redacted_payload == {
        "messages": [
            {"content": "こんにちは [REDACTED:email]"},
            {"content": "SSN [REDACTED:us_ssn]; control 000-12-3456"},
        ]
    }
    assert json.loads(json.dumps(result.redacted_payload)) == result.redacted_payload


def test_email_scan_is_linear_for_long_invalid_local_parts() -> None:
    invalid_candidate = "!" * 60_000 + "owner@" + "a" * 1_000

    result = _service().preview({"input": invalid_candidate})

    assert result.decision == PreSpendDecision.ALLOW
    assert result.redacted_payload == {"input": invalid_candidate}


def test_email_scan_preserves_conservative_boundaries_and_multiple_matches() -> None:
    result = _service().preview(
        {"input": "owner@example.com; second.user@example.test; owner@example.com!"}
    )

    assert result.decision == PreSpendDecision.REDACT
    assert result.redacted_payload == {
        "input": "[REDACTED:email]; [REDACTED:email]; owner@example.com!"
    }


@given(
    st.dictionaries(
        keys=st.sampled_from(
            [
                "capability_id",
                "provider_id",
                "idempotency_key",
                "max_tokens",
                "completion_tokens",
            ]
        ),
        values=st.text(
            alphabet=st.characters(
                blacklist_categories=("Cs",),
                blacklist_characters=("@", "\x00"),
            ),
            max_size=24,
        ),
        max_size=5,
    )
)
@pytest.mark.property
def test_control_fields_and_safe_unicode_are_not_secret_false_positives(
    payload: dict[str, str],
) -> None:
    result = _service().preview(payload)

    assert result.decision == PreSpendDecision.ALLOW
    assert result.redacted_payload == dict(sorted(payload.items()))


@pytest.mark.parametrize("field", ["eos_token", "bos_token", "stop_token"])
def test_model_token_control_fields_are_not_credential_false_positives(field: str) -> None:
    result = _service().preview({field: "</s>"})

    assert result.decision == PreSpendDecision.ALLOW


def test_redaction_that_fails_request_schema_escalates_to_block_without_validator_error() -> None:
    validator_canary = "validator-must-not-escape"

    def validate_email_shape(payload: Any) -> object:
        assert payload == {"email": "[REDACTED:email]"}
        raise ValueError(validator_canary)

    result = _service().preview(
        {"email": "owner@example.com"},
        validate_redacted=validate_email_shape,
    )

    assert result.decision == PreSpendDecision.BLOCK
    assert result.limit_reason == "schema_validation"
    assert result.redacted_payload is None
    assert result.findings[-1].rule == "schema_validation"
    assert validator_canary not in repr(result)


def test_numeric_and_structural_bytes_count_toward_the_limit() -> None:
    result = _service(limits=PreSpendInspectionLimits(max_input_bytes=8)).preview({"n": 10**1000})

    assert result.decision == PreSpendDecision.BLOCK
    assert result.limit_reason == "max_input_bytes"


def test_deadline_is_checked_during_repeated_redaction_callbacks() -> None:
    payload = {"input": " ".join("a@example.com" for _ in range(10_000))}
    result = _service(limits=PreSpendInspectionLimits(timeout_ms=1)).preview(payload)

    assert result.decision == PreSpendDecision.BLOCK
    assert result.limit_reason == "timeout"


def test_non_identifier_path_metadata_never_echoes_mapping_key() -> None:
    sensitive_key = "customer supplied label with spaces"
    result = _service().preview({sensitive_key: _SECRET})

    assert result.decision == PreSpendDecision.BLOCK
    assert result.findings[0].path == '$["<key>"]'
    assert sensitive_key not in result.findings[0].path


def test_concurrent_counter_updates_are_exact() -> None:
    service = _service()
    payloads = [
        {"input": "safe"},
        {"input": "ada@example.com"},
        {"input": _SECRET},
    ] * 40

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(service.inspect, payloads))

    assert len(results) == 120
    assert service.status().counters.to_dict() == {
        "total": 120,
        "allow": 40,
        "redact": 40,
        "block": 40,
    }


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_input_bytes": 0},
        {"max_findings": 0},
        {"max_depth": 0},
        {"max_items": 0},
        {"timeout_ms": 0},
        {"max_input_bytes": True},
        {"max_input_bytes": 1.5},
    ],
)
def test_limits_reject_non_positive_values(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="must be a positive integer"):
        PreSpendInspectionLimits(**kwargs)


def test_compatibility_scanner_preserves_zero_finding_limit() -> None:
    result = scan_pre_spend_payload({"input": _SECRET}, max_findings=0)

    assert result.decision == PreSpendDecision.BLOCK
    assert result.findings == ()
    assert _SECRET not in repr(result)


def test_naive_observation_clock_is_rejected_before_counter_write() -> None:
    service = PreSpendInspectionService(now_factory=lambda: dt.datetime(2026, 9, 1, 12))

    with pytest.raises(ValueError, match="timezone-aware"):
        service.inspect({"input": "safe"})

    assert service.status().counters.total == 0
