"""Notification transports for cost and budget alerts."""

from __future__ import annotations

import importlib
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Protocol

from pitwall.security.redaction import contains_redactable_secret

log = logging.getLogger("pitwall.cost.notifications")
alert_log = logging.getLogger("pitwall.alerts")

RESEND_API_KEY_ENV = "RESEND_API_KEY"
ALERT_FROM_ENV = "PITWALL_ALERT_FROM"
ALERT_TO_ENV = "PITWALL_ALERT_TO"
LEGACY_ALERT_FROM_ENV = "RESEND_SENDER_EMAIL"
LEGACY_ALERT_TO_ENV = "RESEND_BUDGET_ALERT_EMAIL"
NOTIFICATION_DELIVERY_FAILED = "notification_delivery_failed"
_NOTIFICATION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


@dataclass(frozen=True)
class NotificationResult:
    threshold_pct: int | None = None
    email_id: str | None = None
    error: str | None = None
    ok: bool = True


class Notifier(Protocol):
    def send(self, *, subject: str, body: str) -> NotificationResult:
        """Send or record an alert notification."""


class LogNotifier:
    """Out-of-the-box notifier that records alerts in application logs."""

    def send(self, *, subject: str, body: str) -> NotificationResult:
        alert_log.warning("Pitwall alert: %s\n%s", subject, body)
        return NotificationResult(ok=True)


class ResendNotifier:
    """Notifier backed by the Resend Python SDK.

    The SDK is imported only when an alert is actually sent so the base package
    can be installed without the email extra.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        sender: str | None = None,
        recipient: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._sender = sender
        self._recipient = recipient

    def send(self, *, subject: str, body: str) -> NotificationResult:
        try:
            api_key = self._api_key or os.environ.get(RESEND_API_KEY_ENV, "")
            if not api_key:
                return NotificationResult(
                    ok=False,
                    error=f"{RESEND_API_KEY_ENV} environment variable is not set",
                )

            sender = self._sender or _get_alert_sender()
            if not sender:
                return NotificationResult(
                    ok=False,
                    error=(
                        f"{ALERT_FROM_ENV} environment variable is not set "
                        f"(fallback {LEGACY_ALERT_FROM_ENV} is also unset)"
                    ),
                )

            recipient = self._recipient or _get_alert_recipient()
            if not recipient:
                return NotificationResult(
                    ok=False,
                    error=(
                        f"{ALERT_TO_ENV} environment variable is not set "
                        f"(fallback {LEGACY_ALERT_TO_ENV} is also unset)"
                    ),
                )

            resend: Any = importlib.import_module("resend")
            resend.api_key = api_key

            params: dict[str, Any] = {
                "from": sender,
                "to": [recipient],
                "subject": subject,
                "text": body,
            }
            result = resend.Emails.send(params)
            email_id = _extract_email_id(result)
            log.info("Sent alert email via Resend, email_id_present=%s", email_id is not None)
            return NotificationResult(ok=True, email_id=email_id)
        except ModuleNotFoundError as exc:
            if exc.name == "resend" or "resend" in str(exc):
                error = (
                    "resend package is not installed; reinstall Pitwall with the email extra: "
                    "uv tool install --python 3.14.7 "
                    "'pitwall[email] @ https://github.com/Buckeyes22/pitwall/releases/download/v0.3.0a1/pitwall-0.3.0a1-py3-none-any.whl'"  # noqa: E501  # reason: one-line URL so the release validator checks its version
                )
            else:
                error = NOTIFICATION_DELIVERY_FAILED
            log.error("Failed to send alert email via Resend (%s)", type(exc).__name__)
            return NotificationResult(ok=False, error=error)
        except (
            Exception
        ) as exc:  # reason: unexpected Resend failure becomes a failed NotificationResult
            log.error("Failed to send alert email via Resend (%s)", type(exc).__name__)
            return NotificationResult(ok=False, error=NOTIFICATION_DELIVERY_FAILED)


def get_notifier() -> Notifier:
    """Return the configured notifier, defaulting to log-only alerts."""
    if os.environ.get(RESEND_API_KEY_ENV):
        return ResendNotifier()
    return LogNotifier()


def _get_alert_sender() -> str:
    return _get_first_env(ALERT_FROM_ENV, LEGACY_ALERT_FROM_ENV)


def _get_alert_recipient() -> str:
    return _get_first_env(ALERT_TO_ENV, LEGACY_ALERT_TO_ENV)


def _get_first_env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return ""


def _extract_email_id(result: object) -> str | None:
    if isinstance(result, Mapping):
        value = result.get("id")
        return _safe_notification_id(value)
    value = getattr(result, "id", None)
    return _safe_notification_id(value)


def _safe_notification_id(value: object) -> str | None:
    if value is None:
        return None
    candidate = str(value)
    if not _NOTIFICATION_ID_RE.fullmatch(candidate):
        return None
    if contains_redactable_secret(candidate):
        return None
    return candidate


def send_notification_safely(
    *,
    notifier: Notifier | None,
    subject: str,
    body: str,
    context: str,
) -> NotificationResult:
    """Call a notifier without reflecting transport or credential detail."""

    try:
        result = (notifier or get_notifier()).send(subject=subject, body=body)
    except Exception as exc:  # reason: transport detail may contain credentials
        log.error("Failed to send %s notification (%s)", context, type(exc).__name__)
        return NotificationResult(ok=False, error=NOTIFICATION_DELIVERY_FAILED)
    if result.ok:
        return replace(result, email_id=_safe_notification_id(result.email_id), error=None)
    log.error("Failed to send %s notification", context)
    return replace(result, email_id=None, error=NOTIFICATION_DELIVERY_FAILED)


__all__ = [
    "ALERT_FROM_ENV",
    "ALERT_TO_ENV",
    "LEGACY_ALERT_FROM_ENV",
    "LEGACY_ALERT_TO_ENV",
    "LogNotifier",
    "NotificationResult",
    "Notifier",
    "NOTIFICATION_DELIVERY_FAILED",
    "RESEND_API_KEY_ENV",
    "ResendNotifier",
    "get_notifier",
    "send_notification_safely",
]
