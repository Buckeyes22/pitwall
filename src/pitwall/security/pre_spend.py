"""Bounded, non-disclosing inspection for payloads before provider spend."""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import gc
import hashlib
import json
import math
import re
import string
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

REDACTED_SECRET = "[REDACTED:secret]"
REDACTED_PRIVATE_KEY = "[REDACTED:private_key]"
REDACTED_EMAIL = "[REDACTED:email]"
REDACTED_US_SSN = "[REDACTED:us_ssn]"
REDACTED_UNSUPPORTED = "[BLOCKED:unsupported_content]"

DEFAULT_MAX_INPUT_BYTES = 262_144
DEFAULT_MAX_FINDINGS = 32
DEFAULT_MAX_DEPTH = 32
DEFAULT_MAX_ITEMS = 4_096
DEFAULT_TIMEOUT_MS = 50
_MAX_BASE64_CANDIDATE_BYTES = 4_096


class PreSpendDecision(StrEnum):
    """Result of inspecting a payload before cost reservation or egress."""

    ALLOW = "allow"
    REDACT = "redact"
    BLOCK = "block"


class PreSpendFindingKind(StrEnum):
    """Supported, deliberately narrow finding categories."""

    SECRET = "secret"
    PII = "pii"
    UNSUPPORTED = "unsupported"


class PreSpendPolicyMode(StrEnum):
    """How safely redactable findings are handled."""

    BALANCED = "balanced"
    BLOCK = "block"
    REDACT = "redact"


@dataclass(frozen=True, slots=True)
class PreSpendInspectionLimits:
    """Hard work and input limits for one synchronous inspection.

    ``timeout_ms`` budgets the scan's own CPU time, not wall-clock time: it bounds the
    cost of a pathological payload (for example regex backtracking), and a busy host that
    merely delays the scanning thread must not refuse valid input. Garbage collection that
    happens to run on the scanning thread is not the scan's cost either and is excluded.
    """

    max_input_bytes: int = DEFAULT_MAX_INPUT_BYTES
    max_findings: int = DEFAULT_MAX_FINDINGS
    max_depth: int = DEFAULT_MAX_DEPTH
    max_items: int = DEFAULT_MAX_ITEMS
    timeout_ms: int = DEFAULT_TIMEOUT_MS

    def __post_init__(self) -> None:
        for name, value in (
            ("max_input_bytes", self.max_input_bytes),
            ("max_findings", self.max_findings),
            ("max_depth", self.max_depth),
            ("max_items", self.max_items),
            ("timeout_ms", self.timeout_ms),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")

    def to_dict(self) -> dict[str, int]:
        return {
            "max_input_bytes": self.max_input_bytes,
            "max_findings": self.max_findings,
            "max_depth": self.max_depth,
            "max_items": self.max_items,
            "timeout_ms": self.timeout_ms,
        }


@dataclass(frozen=True, slots=True)
class PreSpendRule:
    """Public, secret-free metadata for one bounded rule."""

    rule_id: str
    kind: PreSpendFindingKind
    balanced_action: PreSpendDecision
    description: str

    def to_dict(self) -> dict[str, str]:
        return {
            "rule_id": self.rule_id,
            "kind": self.kind.value,
            "balanced_action": self.balanced_action.value,
            "description": self.description,
        }


@dataclass(frozen=True, slots=True)
class PreSpendFinding:
    """Non-disclosing metadata for one match."""

    kind: PreSpendFindingKind
    rule: str
    path: str
    action: PreSpendDecision
    redacted_preview: str
    fingerprint_sha256: str

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind.value,
            "rule": self.rule,
            "path": self.path,
            "action": self.action.value,
            "redacted_preview": self.redacted_preview,
            "fingerprint_sha256": self.fingerprint_sha256,
        }


@dataclass(frozen=True, slots=True)
class PreSpendPayloadScanResult:
    """Typed decision and safe payload produced by one inspection."""

    decision: PreSpendDecision
    findings: tuple[PreSpendFinding, ...]
    redacted_payload: Any
    mode: PreSpendPolicyMode = PreSpendPolicyMode.BALANCED
    inspected_bytes: int = 0
    elapsed_ms: int = 0
    limited: bool = False
    limit_reason: str | None = None

    @property
    def blocked(self) -> bool:
        return self.decision == PreSpendDecision.BLOCK

    def to_dict(self) -> dict[str, Any]:
        """Return the established compatibility shape used by audit and inference."""
        return {
            "decision": self.decision.value,
            "blocked": self.blocked,
            "findings": [finding.to_dict() for finding in self.findings],
            "redacted_payload": self.redacted_payload,
        }

    def semantic_dict(self) -> dict[str, Any]:
        """Return the shared surface model without including matched content."""
        result = self.to_dict()
        result.update(
            {
                "mode": self.mode.value,
                "inspected_bytes": self.inspected_bytes,
                "elapsed_ms": self.elapsed_ms,
                "limited": self.limited,
                "limit_reason": self.limit_reason,
            }
        )
        return result


@dataclass(frozen=True, slots=True)
class PreSpendInspectionCounters:
    total: int = 0
    allow: int = 0
    redact: int = 0
    block: int = 0

    def record(self, decision: PreSpendDecision) -> PreSpendInspectionCounters:
        return replace(
            self,
            total=self.total + 1,
            allow=self.allow + int(decision == PreSpendDecision.ALLOW),
            redact=self.redact + int(decision == PreSpendDecision.REDACT),
            block=self.block + int(decision == PreSpendDecision.BLOCK),
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "total": self.total,
            "allow": self.allow,
            "redact": self.redact,
            "block": self.block,
        }


@dataclass(frozen=True, slots=True)
class PreSpendLastDecision:
    observed_at: dt.datetime
    decision: PreSpendDecision
    finding_count: int
    rule_ids: tuple[str, ...]
    inspected_bytes: int
    elapsed_ms: int
    limited: bool
    limit_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "observed_at": self.observed_at.isoformat(),
            "decision": self.decision.value,
            "finding_count": self.finding_count,
            "rule_ids": list(self.rule_ids),
            "inspected_bytes": self.inspected_bytes,
            "elapsed_ms": self.elapsed_ms,
            "limited": self.limited,
            "limit_reason": self.limit_reason,
        }


@dataclass(frozen=True, slots=True)
class PreSpendInspectionStatus:
    mode: PreSpendPolicyMode
    limits: PreSpendInspectionLimits
    rules: tuple[PreSpendRule, ...]
    counters: PreSpendInspectionCounters
    last_decision: PreSpendLastDecision | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "limits": self.limits.to_dict(),
            "rules": [rule.to_dict() for rule in self.rules],
            "counters": self.counters.to_dict(),
            "last_decision": (
                self.last_decision.to_dict() if self.last_decision is not None else None
            ),
        }


_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
_LABELED_CLOUD_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:AWS_(?:SECRET_ACCESS_KEY|SECRET_KEY|SESSION_TOKEN|SECURITY_TOKEN|ACCESS_TOKEN)"
    r"|R2_(?:SECRET_ACCESS_KEY|SECRET_KEY|SESSION_TOKEN|ACCESS_KEY|ACCESS_TOKEN))\b"
    r"\s*(?:=|:)\s*(?:\"[^\"\s]{8,}\"|'[^'\s]{8,}'|[-A-Za-z0-9._/+=:]{8,})"
)
_SECRET_VALUE_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("private_key", _PRIVATE_KEY_RE, REDACTED_PRIVATE_KEY),
    (
        "labeled_cloud_secret_assignment",
        _LABELED_CLOUD_SECRET_ASSIGNMENT_RE,
        REDACTED_SECRET,
    ),
    ("openai_style_token", re.compile(r"\bsk-[A-Za-z0-9._-]{16,}\b"), REDACTED_SECRET),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), REDACTED_SECRET),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), REDACTED_SECRET),
    ("gitlab_token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"), REDACTED_SECRET),
    ("slack_token", re.compile(r"\bxox[baprs]?-[A-Za-z0-9-]{20,}\b"), REDACTED_SECRET),
    ("aws_access_key_id", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), REDACTED_SECRET),
    ("hugging_face_token", re.compile(r"\bhf_[A-Za-z0-9_-]{16,}\b"), REDACTED_SECRET),
    ("runpod_api_token", re.compile(r"\brpa_[A-Za-z0-9_-]{16,}\b"), REDACTED_SECRET),
    (
        "url_credentials",
        re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/@\s]+@"),
        REDACTED_SECRET,
    ),
    (
        "bearer_token",
        re.compile(r"(?i)(?<=\bBearer )[A-Za-z0-9._~-]{20,}\b"),
        REDACTED_SECRET,
    ),
)
_EMAIL_LOCAL_CHARACTERS = frozenset(string.ascii_letters + string.digits + ".!#$%&'*+/=?^_`{|}~-")
_EMAIL_DOMAIN_CHARACTERS = frozenset(string.ascii_letters + string.digits + "-")
_US_SSN_RE = re.compile(r"(?<!\d)(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}(?!\d)")
_EXPLICIT_BASE64_RE = re.compile(
    r"(?P<prefix>(?i:base64:|data:text/plain;base64,))"
    r"(?P<encoded>[A-Za-z0-9+/]{4,}={0,2})"
)
_EXPLICIT_BASE64_PREFIX_RE = re.compile(r"(?i:data:text/plain;base64,|base64:)")
_PATH_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CAMEL_CASE_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_FIELD_SEPARATOR_RE = re.compile(r"[^A-Za-z0-9]+")
_SECRET_FIELD_NAMES = frozenset(
    {
        "api_key",
        "apikey",
        "access_token",
        "authorization",
        "aws_session_token",
        "api_token",
        "bearer_token",
        "credential",
        "credentials",
        "database_url",
        "hf_token",
        "id_token",
        "password",
        "pitwall_api_token",
        "pitwall_hf_token",
        "private_key",
        "redis_url",
        "refresh_token",
        "session_token",
        "secret",
        "secret_key",
        "token",
    }
)
_NON_SECRET_FIELD_NAMES = frozenset(
    {
        "capability",
        "capability_id",
        "capability_name",
        "completion_tokens",
        "dry_run",
        "idempotency_key",
        "input_tokens",
        "max_completion_tokens",
        "max_new_tokens",
        "max_output_tokens",
        "max_tokens",
        "output_tokens",
        "prompt_tokens",
        "provider_id",
        "return_colbert",
        "return_dense",
        "return_sparse",
    }
)

PRE_SPEND_RULES: tuple[PreSpendRule, ...] = (
    PreSpendRule(
        "secret_field",
        PreSpendFindingKind.SECRET,
        PreSpendDecision.BLOCK,
        "A value appears under an explicitly secret-bearing field name.",
    ),
    PreSpendRule(
        "private_key",
        PreSpendFindingKind.SECRET,
        PreSpendDecision.BLOCK,
        "PEM private-key material is present.",
    ),
    PreSpendRule(
        "labeled_cloud_secret_assignment",
        PreSpendFindingKind.SECRET,
        PreSpendDecision.BLOCK,
        "A labeled AWS or R2 secret assignment is present.",
    ),
    PreSpendRule(
        "openai_style_token",
        PreSpendFindingKind.SECRET,
        PreSpendDecision.BLOCK,
        "An OpenAI-style secret token shape is present.",
    ),
    PreSpendRule(
        "github_pat",
        PreSpendFindingKind.SECRET,
        PreSpendDecision.BLOCK,
        "A GitHub fine-grained personal access token shape is present.",
    ),
    PreSpendRule(
        "github_token",
        PreSpendFindingKind.SECRET,
        PreSpendDecision.BLOCK,
        "A GitHub token shape is present.",
    ),
    PreSpendRule(
        "gitlab_token",
        PreSpendFindingKind.SECRET,
        PreSpendDecision.BLOCK,
        "A GitLab personal access token shape is present.",
    ),
    PreSpendRule(
        "slack_token",
        PreSpendFindingKind.SECRET,
        PreSpendDecision.BLOCK,
        "A Slack token shape is present.",
    ),
    PreSpendRule(
        "aws_access_key_id",
        PreSpendFindingKind.SECRET,
        PreSpendDecision.BLOCK,
        "An AWS access-key identifier shape is present.",
    ),
    PreSpendRule(
        "bearer_token",
        PreSpendFindingKind.SECRET,
        PreSpendDecision.BLOCK,
        "A long bearer credential is present.",
    ),
    PreSpendRule(
        "hugging_face_token",
        PreSpendFindingKind.SECRET,
        PreSpendDecision.BLOCK,
        "A Hugging Face access token shape is present.",
    ),
    PreSpendRule(
        "runpod_api_token",
        PreSpendFindingKind.SECRET,
        PreSpendDecision.BLOCK,
        "A RunPod API token shape is present.",
    ),
    PreSpendRule(
        "url_credentials",
        PreSpendFindingKind.SECRET,
        PreSpendDecision.BLOCK,
        "A URL contains embedded user information.",
    ),
    PreSpendRule(
        "explicit_base64_secret",
        PreSpendFindingKind.SECRET,
        PreSpendDecision.BLOCK,
        "An explicitly labeled, bounded base64 value decodes to a supported secret shape.",
    ),
    PreSpendRule(
        "email",
        PreSpendFindingKind.PII,
        PreSpendDecision.REDACT,
        "A syntactically complete email address is present.",
    ),
    PreSpendRule(
        "us_ssn",
        PreSpendFindingKind.PII,
        PreSpendDecision.REDACT,
        "A valid-form US Social Security number is present.",
    ),
    PreSpendRule(
        "unsupported_content",
        PreSpendFindingKind.UNSUPPORTED,
        PreSpendDecision.BLOCK,
        "Binary, opaque, over-limit, too-deep, or timed-out content is blocked fail-closed.",
    ),
    PreSpendRule(
        "schema_validation",
        PreSpendFindingKind.UNSUPPORTED,
        PreSpendDecision.BLOCK,
        "A safely redacted payload failed its request-path schema validation.",
    ),
)


class _InspectionLimit(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


_GC_THREAD_STATE = threading.local()


def _record_gc_time(phase: str, _info: dict[str, int]) -> None:
    """Accumulate the CPU time each thread spends inside the garbage collector."""
    if phase == "start":
        _GC_THREAD_STATE.started = time.thread_time_ns()
        return
    started = getattr(_GC_THREAD_STATE, "started", None)
    if started is not None:
        _GC_THREAD_STATE.total = getattr(_GC_THREAD_STATE, "total", 0) + (
            time.thread_time_ns() - started
        )
        _GC_THREAD_STATE.started = None


def _gc_thread_time_ns() -> int:
    """CPU time this thread has spent in garbage collection since the module loaded."""
    total: int = getattr(_GC_THREAD_STATE, "total", 0)
    return total


gc.callbacks.append(_record_gc_time)


def _no_gc() -> int:
    return 0


@dataclass(slots=True)
class _ScanContext:
    mode: PreSpendPolicyMode
    limits: PreSpendInspectionLimits
    start_ns: int
    thread_time_ns: Callable[[], int]
    gc_start_ns: int
    gc_time_ns: Callable[[], int]
    findings: list[PreSpendFinding]
    decisions: set[PreSpendDecision]
    inspected_bytes: int = 0
    inspected_items: int = 0

    def check_work(self, *, depth: int) -> None:
        if depth > self.limits.max_depth:
            raise _InspectionLimit("max_depth")
        self.inspected_items += 1
        if self.inspected_items > self.limits.max_items:
            raise _InspectionLimit("max_items")
        self.check_deadline()

    def check_deadline(self) -> None:
        # CPU time of the scanning thread (the scan is synchronous and single-threaded),
        # so scheduler delay under host load does not count against the budget; time the
        # garbage collector spent on this thread is not the scan's work and is subtracted.
        elapsed_ns = (self.thread_time_ns() - self.start_ns) - (
            self.gc_time_ns() - self.gc_start_ns
        )
        if elapsed_ns > self.limits.timeout_ms * 1_000_000:
            raise _InspectionLimit("timeout")

    def add_bytes(self, size: int) -> None:
        remaining = self.limits.max_input_bytes - self.inspected_bytes
        if size > remaining:
            raise _InspectionLimit("max_input_bytes")
        self.inspected_bytes += size

    def add_json_string(self, value: str) -> None:
        if len(value) > self.limits.max_input_bytes - self.inspected_bytes:
            raise _InspectionLimit("max_input_bytes")
        try:
            encoded = json.dumps(value, ensure_ascii=False).encode("utf-8")
        except UnicodeEncodeError:
            raise _InspectionLimit("invalid_unicode") from None
        self.add_bytes(len(encoded))


class PreSpendInspectionService:
    """Inspect payloads with deterministic bounds and process-local aggregate state."""

    def __init__(
        self,
        *,
        mode: PreSpendPolicyMode = PreSpendPolicyMode.BALANCED,
        limits: PreSpendInspectionLimits | None = None,
        now_factory: Callable[[], dt.datetime] | None = None,
        thread_time_ns: Callable[[], int] | None = None,
        gc_time_ns: Callable[[], int] | None = None,
    ) -> None:
        self._mode = mode
        self._limits = limits or PreSpendInspectionLimits()
        self._now_factory = now_factory or (lambda: dt.datetime.now(dt.UTC))
        self._thread_time_ns = thread_time_ns or time.thread_time_ns
        # A caller that injects its own CPU clock controls the budget exactly; real collector
        # time is only subtracted from the real clock.
        self._gc_time_ns = gc_time_ns or (_gc_thread_time_ns if thread_time_ns is None else _no_gc)
        self._state_lock = threading.Lock()
        self._counters = PreSpendInspectionCounters()
        self._last_decision: PreSpendLastDecision | None = None

    def inspect(
        self,
        payload: Any,
        *,
        validate_redacted: Callable[[Any], object] | None = None,
    ) -> PreSpendPayloadScanResult:
        """Inspect and record aggregate metadata for an actual request."""
        result = self._validated_scan(payload, validate_redacted=validate_redacted)
        now = self._now_factory()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now_factory must return a timezone-aware datetime")
        last_decision = PreSpendLastDecision(
            observed_at=now.astimezone(dt.UTC),
            decision=result.decision,
            finding_count=len(result.findings),
            rule_ids=tuple(sorted({finding.rule for finding in result.findings})),
            inspected_bytes=result.inspected_bytes,
            elapsed_ms=result.elapsed_ms,
            limited=result.limited,
            limit_reason=result.limit_reason,
        )
        with self._state_lock:
            self._counters = self._counters.record(result.decision)
            self._last_decision = last_decision
        return result

    def preview(
        self,
        payload: Any,
        *,
        validate_redacted: Callable[[Any], object] | None = None,
    ) -> PreSpendPayloadScanResult:
        """Inspect without provider, database, audit, counter, or last-decision writes."""
        return self._validated_scan(payload, validate_redacted=validate_redacted)

    def status(self) -> PreSpendInspectionStatus:
        with self._state_lock:
            counters = self._counters
            last_decision = self._last_decision
        return PreSpendInspectionStatus(
            mode=self._mode,
            limits=self._limits,
            rules=PRE_SPEND_RULES,
            counters=counters,
            last_decision=last_decision,
        )

    def _validated_scan(
        self,
        payload: Any,
        *,
        validate_redacted: Callable[[Any], object] | None,
    ) -> PreSpendPayloadScanResult:
        result = self._scan(payload)
        if result.decision != PreSpendDecision.REDACT or validate_redacted is None:
            return result
        try:
            validate_redacted(result.redacted_payload)
        except Exception:  # reason: request validators use heterogeneous exceptions; fail closed.
            context = _ScanContext(
                mode=self._mode,
                limits=self._limits,
                start_ns=0,
                thread_time_ns=self._thread_time_ns,
                gc_start_ns=0,
                gc_time_ns=self._gc_time_ns,
                findings=list(result.findings),
                decisions={PreSpendDecision.BLOCK},
                inspected_bytes=result.inspected_bytes,
            )
            _record_pre_spend_finding(
                context,
                kind=PreSpendFindingKind.UNSUPPORTED,
                rule="schema_validation",
                path="$",
                action=PreSpendDecision.BLOCK,
                redacted_text=REDACTED_UNSUPPORTED,
            )
            return replace(
                result,
                decision=PreSpendDecision.BLOCK,
                findings=tuple(context.findings),
                redacted_payload=None,
                limited=True,
                limit_reason="schema_validation",
            )
        return result

    def _scan(self, payload: Any) -> PreSpendPayloadScanResult:
        start_ns = self._thread_time_ns()
        context = _ScanContext(
            mode=self._mode,
            limits=self._limits,
            start_ns=start_ns,
            thread_time_ns=self._thread_time_ns,
            gc_start_ns=self._gc_time_ns(),
            gc_time_ns=self._gc_time_ns,
            findings=[],
            decisions=set(),
        )
        limited = False
        limit_reason: str | None = None
        try:
            redacted_payload = _scan_pre_spend_value(
                payload,
                path="$",
                field_name=None,
                depth=0,
                context=context,
            )
        except _InspectionLimit as exc:
            limited = True
            limit_reason = exc.reason
            redacted_payload = None
            _record_pre_spend_finding(
                context,
                kind=PreSpendFindingKind.UNSUPPORTED,
                rule="unsupported_content",
                path="$",
                action=PreSpendDecision.BLOCK,
                redacted_text=REDACTED_UNSUPPORTED,
            )

        decision = _final_decision(context.decisions)
        elapsed_ms = max(
            0,
            ((self._thread_time_ns() - start_ns) - (self._gc_time_ns() - context.gc_start_ns))
            // 1_000_000,
        )
        return PreSpendPayloadScanResult(
            decision=decision,
            findings=tuple(context.findings),
            redacted_payload=redacted_payload,
            mode=self._mode,
            inspected_bytes=context.inspected_bytes,
            elapsed_ms=elapsed_ms,
            limited=limited,
            limit_reason=limit_reason,
        )


_DEFAULT_PRE_SPEND_INSPECTION_SERVICE: PreSpendInspectionService | None = None
_DEFAULT_PRE_SPEND_INSPECTION_SERVICE_LOCK = threading.Lock()


def build_pre_spend_inspection_service(
    *,
    mode: PreSpendPolicyMode | str,
) -> PreSpendInspectionService:
    """Build an inspection service from a validated transport-neutral mode."""
    try:
        policy_mode = PreSpendPolicyMode(mode)
    except ValueError:
        raise ValueError("pre-spend mode must be balanced, block, or redact") from None
    return PreSpendInspectionService(mode=policy_mode)


def get_pre_spend_inspection_service() -> PreSpendInspectionService:
    """Return the process-local inspection service shared by operator surfaces."""
    global _DEFAULT_PRE_SPEND_INSPECTION_SERVICE
    with _DEFAULT_PRE_SPEND_INSPECTION_SERVICE_LOCK:
        if _DEFAULT_PRE_SPEND_INSPECTION_SERVICE is None:
            from pitwall.config import get_settings

            _DEFAULT_PRE_SPEND_INSPECTION_SERVICE = build_pre_spend_inspection_service(
                mode=get_settings().pitwall_pre_spend_mode
            )
        return _DEFAULT_PRE_SPEND_INSPECTION_SERVICE


def parse_pre_spend_json(raw_payload: str, *, max_input_bytes: int) -> Any:
    """Parse operator-preview JSON only after enforcing a UTF-8 byte bound."""
    try:
        encoded_size = len(raw_payload.encode("utf-8"))
    except UnicodeEncodeError:
        raise ValueError("payload must be valid UTF-8 JSON") from None
    if encoded_size > max_input_bytes:
        raise ValueError("payload exceeds the configured guardrail input limit")
    try:
        return json.loads(raw_payload)
    except json.JSONDecodeError, RecursionError, ValueError:
        raise ValueError("payload must be valid JSON") from None


def scan_pre_spend_payload(
    payload: Any,
    *,
    max_findings: int = DEFAULT_MAX_FINDINGS,
) -> PreSpendPayloadScanResult:
    """Compatibility entry point using the bounded balanced policy."""
    if not isinstance(max_findings, int) or isinstance(max_findings, bool) or max_findings < 0:
        raise ValueError("max_findings must be a non-negative integer")
    service = PreSpendInspectionService(
        limits=replace(PreSpendInspectionLimits(), max_findings=max(1, max_findings))
    )
    result = service.preview(payload)
    return replace(result, findings=()) if max_findings == 0 else result


def _scan_pre_spend_value(
    value: Any,
    *,
    path: str,
    field_name: str | None,
    depth: int,
    context: _ScanContext,
) -> Any:
    context.check_work(depth=depth)
    if isinstance(value, str):
        context.add_json_string(value)
        if _is_secret_field_name(field_name) and value.strip():
            _record_pre_spend_finding(
                context,
                kind=PreSpendFindingKind.SECRET,
                rule="secret_field",
                path=path,
                action=_finding_action(context.mode, PreSpendDecision.BLOCK),
                redacted_text=REDACTED_SECRET,
            )
            return REDACTED_SECRET
        return _scan_pre_spend_string(value, path=path, context=context)
    if _is_secret_field_name(field_name) and not _is_empty_secret_value(value):
        _record_pre_spend_finding(
            context,
            kind=PreSpendFindingKind.SECRET,
            rule="secret_field",
            path=path,
            action=_finding_action(context.mode, PreSpendDecision.BLOCK),
            redacted_text=REDACTED_SECRET,
        )
        raise _InspectionLimit("secret_container")
    if isinstance(value, dict):
        if len(value) > context.limits.max_items - context.inspected_items:
            raise _InspectionLimit("max_items")
        keys = list(value)
        if not all(isinstance(key, str) for key in keys):
            raise _InspectionLimit("opaque_mapping_key")
        context.add_bytes(2)
        redacted_mapping: dict[str, Any] = {}
        for index, key in enumerate(sorted(keys)):
            if index:
                context.add_bytes(1)
            if "\x00" in key:
                raise _InspectionLimit("null_byte")
            context.add_json_string(key)
            context.add_bytes(1)
            if _contains_sensitive_text(key):
                raise _InspectionLimit("sensitive_mapping_key")
            redacted_mapping[key] = _scan_pre_spend_value(
                value[key],
                path=_child_path(path, key),
                field_name=key,
                depth=depth + 1,
                context=context,
            )
        return redacted_mapping
    if isinstance(value, list | tuple):
        if len(value) > context.limits.max_items - context.inspected_items:
            raise _InspectionLimit("max_items")
        context.add_bytes(2)
        redacted_list: list[Any] = []
        for index, item in enumerate(value):
            if index:
                context.add_bytes(1)
            redacted_list.append(
                _scan_pre_spend_value(
                    item,
                    path=f"{path}[{index}]",
                    field_name=None,
                    depth=depth + 1,
                    context=context,
                )
            )
        return redacted_list
    if value is None:
        context.add_bytes(4)
        return value
    if isinstance(value, bool):
        context.add_bytes(4 if value else 5)
        return value
    if isinstance(value, int):
        if value.bit_length() > context.limits.max_input_bytes * 4:
            raise _InspectionLimit("max_input_bytes")
        try:
            rendered_int = str(value)
        except ValueError:
            raise _InspectionLimit("max_input_bytes") from None
        context.add_bytes(len(rendered_int))
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _InspectionLimit("invalid_number")
        context.add_bytes(len(repr(value)))
        return value
    raise _InspectionLimit("opaque_content")


def _scan_pre_spend_string(
    value: str,
    *,
    path: str,
    context: _ScanContext,
) -> str:
    if "\x00" in value:
        raise _InspectionLimit("null_byte")

    redacted = _redact_explicit_base64(value, path=path, context=context)
    context.check_deadline()
    for rule, pattern, replacement_text in _SECRET_VALUE_PATTERNS:
        redacted = _redact_pattern(
            redacted,
            pattern=pattern,
            replacement_text=replacement_text,
            kind=PreSpendFindingKind.SECRET,
            rule=rule,
            path=path,
            action=_finding_action(context.mode, PreSpendDecision.BLOCK),
            context=context,
        )
        context.check_deadline()
    redacted = _redact_email(
        redacted,
        path=path,
        action=_finding_action(context.mode, PreSpendDecision.REDACT),
        context=context,
    )
    context.check_deadline()
    redacted = _redact_pattern(
        redacted,
        pattern=_US_SSN_RE,
        replacement_text=REDACTED_US_SSN,
        kind=PreSpendFindingKind.PII,
        rule="us_ssn",
        path=path,
        action=_finding_action(context.mode, PreSpendDecision.REDACT),
        context=context,
    )
    context.check_deadline()
    return redacted


def _redact_explicit_base64(value: str, *, path: str, context: _ScanContext) -> str:
    lowered = value.lower()
    if "base64:" not in lowered and "data:text/plain;base64," not in lowered:
        return value

    prefix_count = len(_EXPLICIT_BASE64_PREFIX_RE.findall(value))

    def replace_match(match: re.Match[str]) -> str:
        context.check_deadline()
        encoded = match.group("encoded")
        if len(encoded) > _MAX_BASE64_CANDIDATE_BYTES * 2:
            raise _InspectionLimit("encoded_value_too_large")
        try:
            decoded_bytes = base64.b64decode(encoded, validate=True)
        except binascii.Error, ValueError:
            raise _InspectionLimit("invalid_encoded_content") from None
        if len(decoded_bytes) > _MAX_BASE64_CANDIDATE_BYTES:
            raise _InspectionLimit("encoded_value_too_large")
        try:
            decoded = decoded_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raise _InspectionLimit("encoded_binary") from None
        if not _contains_supported_secret(decoded):
            return match.group(0)
        _record_pre_spend_finding(
            context,
            kind=PreSpendFindingKind.SECRET,
            rule="explicit_base64_secret",
            path=path,
            action=_finding_action(context.mode, PreSpendDecision.BLOCK),
            redacted_text=REDACTED_SECRET,
        )
        return f"{match.group('prefix')}{REDACTED_SECRET}"

    redacted, matched_count = _EXPLICIT_BASE64_RE.subn(replace_match, value)
    if matched_count != prefix_count:
        raise _InspectionLimit("invalid_encoded_content")
    return redacted


def _contains_supported_secret(value: str) -> bool:
    return any(pattern.search(value) is not None for _, pattern, _ in _SECRET_VALUE_PATTERNS)


def _contains_sensitive_text(value: str) -> bool:
    return (
        _contains_supported_secret(value)
        or next(_iter_email_spans(value), None) is not None
        or _US_SSN_RE.search(value) is not None
    )


def _iter_email_spans(value: str) -> Iterator[tuple[int, int]]:
    """Yield conservative email spans with a linear, non-backtracking scan."""

    search_from = 0
    value_length = len(value)
    while search_from < value_length:
        at_index = value.find("@", search_from)
        if at_index < 0:
            return

        start = at_index
        while start > 0 and value[start - 1] in _EMAIL_LOCAL_CHARACTERS:
            start -= 1
        if start == at_index:
            search_from = at_index + 1
            continue

        end = at_index + 1
        while end < value_length and value[end] in _EMAIL_DOMAIN_CHARACTERS:
            end += 1
        if end == at_index + 1:
            search_from = at_index + 1
            continue

        dotted_labels = 0
        while end < value_length and value[end] == ".":
            label_start = end + 1
            label_end = label_start
            while label_end < value_length and value[label_end] in _EMAIL_DOMAIN_CHARACTERS:
                label_end += 1
            if label_end == label_start:
                break
            dotted_labels += 1
            end = label_end

        if dotted_labels and (end == value_length or value[end] not in _EMAIL_LOCAL_CHARACTERS):
            yield start, end
            search_from = end
        else:
            search_from = at_index + 1


def _redact_email(
    value: str,
    *,
    path: str,
    action: PreSpendDecision,
    context: _ScanContext,
) -> str:
    parts: list[str] = []
    consumed = 0
    for start, end in _iter_email_spans(value):
        context.check_deadline()
        parts.extend((value[consumed:start], REDACTED_EMAIL))
        consumed = end
        _record_pre_spend_finding(
            context,
            kind=PreSpendFindingKind.PII,
            rule="email",
            path=path,
            action=action,
            redacted_text=REDACTED_EMAIL,
        )
    if not parts:
        return value
    parts.append(value[consumed:])
    return "".join(parts)


def _redact_pattern(
    value: str,
    *,
    pattern: re.Pattern[str],
    replacement_text: str,
    kind: PreSpendFindingKind,
    rule: str,
    path: str,
    action: PreSpendDecision,
    context: _ScanContext,
) -> str:
    def replace_match(match: re.Match[str]) -> str:
        context.check_deadline()
        _record_pre_spend_finding(
            context,
            kind=kind,
            rule=rule,
            path=path,
            action=action,
            redacted_text=replacement_text,
        )
        return replacement_text

    return pattern.sub(replace_match, value)


def _record_pre_spend_finding(
    context: _ScanContext,
    *,
    kind: PreSpendFindingKind,
    rule: str,
    path: str,
    action: PreSpendDecision,
    redacted_text: str,
) -> None:
    context.decisions.add(action)
    if len(context.findings) >= context.limits.max_findings:
        return
    fingerprint = hashlib.sha256(f"{rule}\0{path}".encode()).hexdigest()
    context.findings.append(
        PreSpendFinding(
            kind=kind,
            rule=rule,
            path=path,
            action=action,
            redacted_preview=_preview(redacted_text),
            fingerprint_sha256=fingerprint,
        )
    )


def _finding_action(
    mode: PreSpendPolicyMode,
    balanced_action: PreSpendDecision,
) -> PreSpendDecision:
    if mode == PreSpendPolicyMode.BLOCK:
        return PreSpendDecision.BLOCK
    if mode == PreSpendPolicyMode.REDACT:
        return PreSpendDecision.REDACT
    return balanced_action


def _final_decision(decisions: set[PreSpendDecision]) -> PreSpendDecision:
    if PreSpendDecision.BLOCK in decisions:
        return PreSpendDecision.BLOCK
    if PreSpendDecision.REDACT in decisions:
        return PreSpendDecision.REDACT
    return PreSpendDecision.ALLOW


def _preview(value: str) -> str:
    max_preview_chars = 96
    if len(value) <= max_preview_chars:
        return value
    return value[: max_preview_chars - 3] + "..."


def _is_secret_field_name(field_name: str | None) -> bool:
    if field_name is None:
        return False
    camel_separated = _CAMEL_CASE_BOUNDARY_RE.sub("_", field_name.strip())
    normalized = _FIELD_SEPARATOR_RE.sub("_", camel_separated).strip("_").casefold()
    if normalized in _NON_SECRET_FIELD_NAMES:
        return False
    if normalized in _SECRET_FIELD_NAMES:
        return True
    return any(
        token in normalized
        for token in (
            "api_key",
            "access_key",
            "auth_token",
            "bearer",
            "credential",
            "password",
            "secret",
        )
    )


def _is_empty_secret_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, dict | list | tuple):
        return len(value) == 0
    return False


def _child_path(parent: str, key: str) -> str:
    if len(key) <= 64 and _PATH_IDENTIFIER_RE.fullmatch(key):
        return f"{parent}.{key}"
    return f'{parent}["<key>"]'


__all__ = [
    "PRE_SPEND_RULES",
    "PreSpendDecision",
    "PreSpendFinding",
    "PreSpendFindingKind",
    "PreSpendInspectionCounters",
    "PreSpendInspectionLimits",
    "PreSpendInspectionService",
    "PreSpendInspectionStatus",
    "PreSpendLastDecision",
    "PreSpendPayloadScanResult",
    "PreSpendPolicyMode",
    "PreSpendRule",
    "build_pre_spend_inspection_service",
    "get_pre_spend_inspection_service",
    "parse_pre_spend_json",
    "scan_pre_spend_payload",
]
