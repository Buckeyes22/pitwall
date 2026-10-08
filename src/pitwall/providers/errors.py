"""Provider error types shared by every adapter that reports a quota or rate limit."""

from __future__ import annotations

import datetime as dt
from typing import Literal

QuotaReason = Literal[
    "quota_exhausted", "rate_limit_exceeded", "model_capacity", "permanent_ban", "billing_state"
]


class ProviderQuotaExhausted(RuntimeError):
    """A provider refused a request because a quota or rate limit is spent.

    Routing catches this type, whichever adapter raised it, and locks the
    ``(provider, model)`` pair until ``reset_at`` (or a default backoff when it is ``None``).
    """

    reason: QuotaReason
    reset_at: dt.datetime | None


__all__ = ["ProviderQuotaExhausted", "QuotaReason"]
