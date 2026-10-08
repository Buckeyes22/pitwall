"""Typed rows for the synced free-tier catalog (ADR 0007 vocabulary)."""

from __future__ import annotations

from dataclasses import dataclass

FREE_TYPES = frozenset(
    {
        "recurring-daily",
        "recurring-monthly",
        "recurring-credit",
        "recurring-uncapped",
        "one-time-initial",
        "keyless",
        "discontinued",
    }
)
TOS_VERDICTS = frozenset({"ok", "caution", "ambiguous", "avoid", "unknown"})
STEADY_MONTHLY = frozenset({"recurring-daily", "recurring-monthly"})
RECURRING_CREDIT = frozenset({"recurring-credit"})
ONE_TIME = frozenset({"one-time-initial"})
UNCAPPED = frozenset({"recurring-uncapped"})


@dataclass(frozen=True, slots=True)
class CatalogRow:
    provider: str
    model_id: str
    display_name: str
    monthly_tokens: int
    credit_tokens: int
    free_type: str
    pool_key: str | None
    tos: str
    base_url: str
    upstream_format: str = "openai"
    executor: str = "default"
    auth_type: str = "apikey"
    trains_on_prompts: bool = False
    hard_stop_guaranteed: bool = False
    eligibility_gate: str | None = None

    def __post_init__(self) -> None:
        if self.free_type not in FREE_TYPES:
            raise ValueError(
                f"unknown freeType {self.free_type!r} for {self.provider}/{self.model_id}"
            )
        if self.tos not in TOS_VERDICTS:
            raise ValueError(f"unknown tos {self.tos!r} for {self.provider}/{self.model_id}")
        if not self.base_url.startswith("https://") and not self.base_url.startswith(
            "http://127.0.0.1"
        ):
            raise ValueError(f"base_url must be https or loopback: {self.base_url}")

    @property
    def routable(self) -> bool:
        """ADR 0007 rule 2, evaluated on catalog evidence alone."""
        if self.tos in {"avoid", "unknown"} or self.eligibility_gate is not None:
            return False
        if self.free_type == "discontinued":
            return False
        return self.hard_stop_guaranteed or self.free_type == "keyless"

    @property
    def direct_ok(self) -> bool:
        """True when the upstream speaks plain OpenAI chat, the default executor
        can serve it, and the credential type is a simple header (apikey/none).
        Task 19 retargets non-direct rows through the fork; this flag is recorded
        but does not affect ``enabled`` (ADR 0007).
        """
        return (
            self.upstream_format == "openai"
            and self.executor == "default"
            and self.auth_type in {"apikey", "none"}
        )

    @property
    def seed_name(self) -> str:
        slug = self.model_id.split("/")[-1].lower().replace("_", "-").replace(".", "-")
        return f"gw-{self.provider}-{slug}"
