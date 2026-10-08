"""Audit checks 17-19: pre-spend guardrails and the Policy-as-Code gate."""

from __future__ import annotations

import json

from pitwall.audit._common import (
    AuditConfig,
    AuditSeverity,
    CheckFailed,
    _findings_evidence,
    _pre_spend_payloads,
)
from pitwall.security.pre_spend import (
    PreSpendDecision,
    PreSpendFindingKind,
    scan_pre_spend_payload,
)


def check_17_pre_spend_secret_guardrail(cfg: AuditConfig) -> str:
    token_probe = scan_pre_spend_payload(
        {"prompt": "token=sk-test_1234567890abcdef1234567890abcdef"}  # pragma: allowlist secret
    )
    if token_probe.decision != PreSpendDecision.BLOCK:
        raise CheckFailed(
            17,
            "pre-spend scanner did not block API-token-shaped payload",
            severity=AuditSeverity.CRITICAL,
        )

    private_key_probe = scan_pre_spend_payload(
        {
            "prompt": "\n".join(
                (
                    "-----BEGIN PRIVATE KEY-----",  # pragma: allowlist secret
                    "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC",  # pragma: allowlist secret
                    "-----END PRIVATE KEY-----",
                )
            )
        }
    )
    if private_key_probe.decision != PreSpendDecision.BLOCK:
        raise CheckFailed(
            17,
            "pre-spend scanner did not block private-key material",
            severity=AuditSeverity.CRITICAL,
        )

    for index, payload in enumerate(_pre_spend_payloads(cfg)):
        result = scan_pre_spend_payload(payload)
        secret_findings = [
            finding for finding in result.findings if finding.kind == PreSpendFindingKind.SECRET
        ]
        if result.decision == PreSpendDecision.BLOCK:
            raise CheckFailed(
                17,
                f"pre-spend payload fixture {index} contains secret material",
                severity=AuditSeverity.CRITICAL,
                evidence=_findings_evidence(secret_findings or list(result.findings)),
                remediation=(
                    "Remove secrets from inbound inference/capability payloads; "
                    "pass credentials through configured server-side secret channels only."
                ),
            )
    return "pre-spend secret guardrail blocks API keys, tokens, and private keys"


def check_18_pre_spend_pii_redaction(cfg: AuditConfig) -> str:
    email_probe = scan_pre_spend_payload({"prompt": "contact ada.lovelace@example.com"})
    if email_probe.decision != PreSpendDecision.REDACT:
        raise CheckFailed(
            18,
            "pre-spend scanner did not redact email PII",
            severity=AuditSeverity.HIGH,
        )
    if "ada.lovelace@example.com" in json.dumps(email_probe.to_dict(), sort_keys=True):
        raise CheckFailed(
            18,
            "pre-spend scanner returned raw email PII in structured output",
            severity=AuditSeverity.HIGH,
        )

    for index, payload in enumerate(_pre_spend_payloads(cfg)):
        result = scan_pre_spend_payload(payload)
        pii_findings = [
            finding for finding in result.findings if finding.kind == PreSpendFindingKind.PII
        ]
        if pii_findings:
            raise CheckFailed(
                18,
                f"pre-spend payload fixture {index} contains unredacted PII",
                severity=AuditSeverity.HIGH,
                evidence=_findings_evidence(pii_findings),
                remediation=(
                    "Redact PII before storing audit fixtures or forwarding payloads "
                    "to provider execution paths."
                ),
            )
    return "pre-spend PII guardrail redacts emails before spend"


def check_19_policy_as_code_audit_gate(cfg: AuditConfig) -> str:
    from pitwall.policy import evaluate_default_policies

    result = evaluate_default_policies(cfg)
    if not result.allowed:
        raise CheckFailed(
            19,
            f"policy-as-code audit gate denied {len(result.violations)} finding(s)",
            severity=AuditSeverity.HIGH,
            evidence=json.dumps(
                result.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            ),
            remediation=(
                "Update capability, provider, or workload configuration to satisfy "
                "packaged Policy-as-Code rules before invoking RunPod spend paths."
            ),
        )
    return "policy-as-code audit gate allowed configured providers and workloads"
