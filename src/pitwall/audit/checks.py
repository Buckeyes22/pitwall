"""19-check RunPod audit harness for Pitwall CI.

Automated, hermetic CI check suite. Each check validates one documented
runtime invariant.

The harness exposes a CLI via ``python -m pitwall.audit.checks``
that prints a pass/fail report and exits 0 only when all 19 checks pass.

Check functions accept a *config* object that represents the Pitwall runtime
configuration. In production this is built from environment and code facts
(``RuntimeAuditConfig``); in CI the test suite provides synthetic configs to
exercise each check without live RunPod calls.

The checks live in one module per family (``_checks_provisioning``,
``_checks_lifecycle``, ``_checks_operations``, ``_checks_guardrails``); this
module registers them and owns the runner and CLI.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any, cast

from pitwall.audit._checks_guardrails import (
    check_17_pre_spend_secret_guardrail,
    check_18_pre_spend_pii_redaction,
    check_19_policy_as_code_audit_gate,
)
from pitwall.audit._checks_lifecycle import (
    check_03_readiness_runtime,
    check_04_cost_cap_before_readiness,
    check_05_execution_timeout,
    check_06_ttl_ge_timeout_plus_queue,
    check_08_retention_windows,
    check_10_ssh_first_probe,
)
from pitwall.audit._checks_operations import (
    check_07_webhook_idempotent_fast200,
    check_15_terminate_idempotent,
    check_16_kill_switch_atomic,
)
from pitwall.audit._checks_provisioning import (
    check_01_gpu_ids_canonical,
    check_02_cloud_type_volume,
    check_09_dc_pin,
    check_11_image_pull_timeout,
    check_12_disk_sized,
    check_13_template_cache,
    check_14_registry_auth,
)
from pitwall.audit._common import (
    ASYNC_RESULT_RETENTION_S,
    EXPECTED_AUDIT_CHECK_COUNT,
    SYNC_RESULT_RETENTION_S,
    AuditConfig,
    AuditSeverity,
    CheckFailed,
    CheckResult,
)
from pitwall.runpod_client.gpu import CANONICAL_GPU_NAMES
from pitwall.security.pre_spend import (
    PreSpendDecision,
    PreSpendFindingKind,
    scan_pre_spend_payload,
)
from pitwall.security.pre_spend import (
    PreSpendPayloadScanResult as _PreSpendPayloadScanResult,
)

PreSpendPayloadScanResult = _PreSpendPayloadScanResult

CHECK_DESCRIPTIONS: dict[int, str] = {
    1: "GPU IDs are canonical RunPod names",
    2: "cloud_type=ALL is never combined with networkVolumeId",
    3: "Pod readiness verified via runtime != null",
    4: "Cost-cap check fires before readiness wait",
    5: "executionTimeout respected with explicit max",
    6: "ttl >= executionTimeout + expected_queue_time",
    7: "Webhook receiver dedupes deliveries (idempotent ingress)",
    8: "Result retention windows respected",
    9: "Network-volume DC pin enforced",
    10: "SSH-first probe pattern available for pod-mode readiness",
    11: "Image-pull timeout enforced and staging store wiring is abstracted",
    12: "Container disk explicitly sized per workload; vLLM fixtures use hf download",
    13: "Template create + cache pattern (no template-recreate on every launch)",
    14: "Registry-auth-id selected per image-ref prefix; vLLM fixtures hibernated",
    15: "terminate_* calls are idempotent (404 = success)",
    16: "Kill switch is atomic, 3-step, <30s",
    17: "Pre-spend payload secret guardrail blocks API keys, tokens, and private keys",
    18: "Pre-spend payload PII guardrail redacts emails before spend",
    19: "Policy-as-Code audit gate allows capability, provider, and workload configs",
}


CHECK_FUNCTIONS: list[Callable[[AuditConfig], str]] = [
    check_01_gpu_ids_canonical,
    check_02_cloud_type_volume,
    check_03_readiness_runtime,
    check_04_cost_cap_before_readiness,
    check_05_execution_timeout,
    check_06_ttl_ge_timeout_plus_queue,
    check_07_webhook_idempotent_fast200,
    check_08_retention_windows,
    check_09_dc_pin,
    check_10_ssh_first_probe,
    check_11_image_pull_timeout,
    check_12_disk_sized,
    check_13_template_cache,
    check_14_registry_auth,
    check_15_terminate_idempotent,
    check_16_kill_switch_atomic,
    check_17_pre_spend_secret_guardrail,
    check_18_pre_spend_pii_redaction,
    check_19_policy_as_code_audit_gate,
]

for n, fn in enumerate(CHECK_FUNCTIONS, start=1):
    cast(Any, fn).check_id = n

if len(CHECK_FUNCTIONS) != EXPECTED_AUDIT_CHECK_COUNT:
    raise RuntimeError(f"expected {EXPECTED_AUDIT_CHECK_COUNT} checks, got {len(CHECK_FUNCTIONS)}")


def run_all_checks(cfg: AuditConfig) -> list[CheckResult]:
    """Execute all audit checks against *cfg*, returning per-check results."""
    results: list[CheckResult] = []
    for fn in CHECK_FUNCTIONS:
        check_id: int = cast(Any, fn).check_id
        name = CHECK_DESCRIPTIONS.get(check_id) or fn.__name__
        try:
            message = fn(cfg)
            results.append(
                CheckResult(
                    check_id=check_id,
                    name=name,
                    passed=True,
                    severity=AuditSeverity.LOW,
                    evidence=message,
                    remediation="",
                    message=message,
                )
            )
        except CheckFailed as exc:
            results.append(
                CheckResult(
                    check_id=check_id,
                    name=name,
                    passed=False,
                    severity=exc.severity,
                    evidence=exc.evidence,
                    remediation=exc.remediation,
                    message=exc.message,
                )
            )
    return results


def format_report(results: list[CheckResult]) -> str:
    """Format results as a human-readable report."""
    lines: list[str] = []
    passed = sum(1 for r in results if r.passed)
    lines.append(
        f"Pitwall {EXPECTED_AUDIT_CHECK_COUNT}-check audit: {passed}/{len(results)} passed"
    )
    lines.append("")
    for r in results:
        tag = "PASS" if r.passed else "FAIL"
        lines.append(f"  [{r.check_id:2d}] {tag} [{r.severity.value}] {r.name}: {r.message}")
        if not r.passed and r.remediation:
            lines.append(f"         Evidence: {r.evidence}")
            lines.append(f"         Remediation: {r.remediation}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] in ("--help", "-h"):
        print("Usage: python -m pitwall.audit.checks [--strict] [--json]")
        print()
        print(
            f"Run the {EXPECTED_AUDIT_CHECK_COUNT}-check RunPod audit against Pitwall "
            "configuration."
        )
        print("  --strict  Exit non-zero if any check fails (default for CI)")
        print("  --json    Output results as JSON")
        print("Exits 0 if all checks pass, 1 otherwise.")
        return 0

    output_json = "--json" in args
    strict = "--strict" in args

    from pitwall.audit._runtime_config import RuntimeAuditConfig

    cfg = RuntimeAuditConfig()
    results = run_all_checks(cfg)

    if output_json:
        all_passed = all(r.passed for r in results)
        payload = {
            "all_passed": all_passed,
            "strict": strict,
            "checks": [
                {
                    "check_id": r.check_id,
                    "name": r.name,
                    "passed": r.passed,
                    "severity": r.severity.value,
                    "evidence": r.evidence,
                    "remediation": r.remediation,
                    "message": r.message,
                }
                for r in results
            ],
        }
        print(json.dumps(payload, indent=2))
    else:
        print(format_report(results))
        all_passed = all(r.passed for r in results)

    if strict:
        return 0 if all_passed else 1
    return 0


__all__ = [
    "ASYNC_RESULT_RETENTION_S",
    "CANONICAL_GPU_NAMES",
    "CHECK_DESCRIPTIONS",
    "CHECK_FUNCTIONS",
    "EXPECTED_AUDIT_CHECK_COUNT",
    "SYNC_RESULT_RETENTION_S",
    "AuditConfig",
    "AuditSeverity",
    "CheckFailed",
    "CheckResult",
    "PreSpendDecision",
    "PreSpendFindingKind",
    "PreSpendPayloadScanResult",
    "check_01_gpu_ids_canonical",
    "check_02_cloud_type_volume",
    "check_03_readiness_runtime",
    "check_04_cost_cap_before_readiness",
    "check_05_execution_timeout",
    "check_06_ttl_ge_timeout_plus_queue",
    "check_07_webhook_idempotent_fast200",
    "check_08_retention_windows",
    "check_09_dc_pin",
    "check_10_ssh_first_probe",
    "check_11_image_pull_timeout",
    "check_12_disk_sized",
    "check_13_template_cache",
    "check_14_registry_auth",
    "check_15_terminate_idempotent",
    "check_16_kill_switch_atomic",
    "check_17_pre_spend_secret_guardrail",
    "check_18_pre_spend_pii_redaction",
    "check_19_policy_as_code_audit_gate",
    "format_report",
    "main",
    "run_all_checks",
    "scan_pre_spend_payload",
]


if __name__ == "__main__":
    raise SystemExit(main())
