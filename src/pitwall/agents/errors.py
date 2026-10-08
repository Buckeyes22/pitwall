"""Runtime exceptions with stable shim-facing exit semantics."""

from __future__ import annotations


class RoutingError(RuntimeError):
    """Base class for expected routing failures."""


class UsageError(RoutingError):
    """The public shim invocation is invalid."""


class PromptError(RoutingError):
    """The requested prompt source cannot be read."""


class HarnessNotFoundError(RoutingError):
    """A harness executable cannot be resolved."""


EX_CONFIG = 78
# A harness reported success (exit 0) without doing the work: a tool permission was
# auto-denied, or it produced no answer. The run's softDenialReason says which.
EX_NOPERM = 77


class ProfileConfigError(RoutingError):
    """A route resolves but its configuration is incomplete (exit 78, no ledger row)."""


class ProfileSyncError(RoutingError):
    """A harness config cannot be safely rewritten by profiles sync."""
