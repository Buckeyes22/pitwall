"""Normalized token usage and parsed harness output."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None
    complete: bool = True
    source: str = "harness-json"

    def __add__(self, other: TokenUsage) -> TokenUsage:
        """Sum known counts while retaining unknown values as unknown."""
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=_sum_optional(self.cache_read_tokens, other.cache_read_tokens),
            cache_write_tokens=_sum_optional(self.cache_write_tokens, other.cache_write_tokens),
            reasoning_tokens=_sum_optional(self.reasoning_tokens, other.reasoning_tokens),
            total_tokens=_sum_optional(self.total_tokens, other.total_tokens),
            cost_usd=_sum_optional(self.cost_usd, other.cost_usd),
            complete=self.complete and other.complete,
            source=self.source,
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "cacheReadTokens": self.cache_read_tokens,
            "cacheWriteTokens": self.cache_write_tokens,
            "reasoningTokens": self.reasoning_tokens,
            "totalTokens": self.total_tokens,
            "costUsd": self.cost_usd,
            "complete": self.complete,
            "source": self.source,
        }

    @classmethod
    def from_public_dict(cls, data: Mapping[str, Any]) -> TokenUsage:
        """Build a usage value from the public camelCase representation."""
        input_tokens = as_int(data.get("inputTokens"))
        output_tokens = as_int(data.get("outputTokens"))
        if input_tokens is None or output_tokens is None:
            raise ValueError(
                "public token usage requires non-negative inputTokens and outputTokens"
            )

        optional_counts = {
            name: as_int(data.get(key))
            for name, key in (
                ("cache_read_tokens", "cacheReadTokens"),
                ("cache_write_tokens", "cacheWriteTokens"),
                ("reasoning_tokens", "reasoningTokens"),
                ("total_tokens", "totalTokens"),
            )
        }
        cost = data.get("costUsd")
        if isinstance(cost, bool) or not isinstance(cost, (int, float)):
            cost_usd = None
        elif math.isfinite(cost) and cost >= 0:
            cost_usd = float(cost)
        else:
            cost_usd = None
        complete = data.get("complete", True)
        source = data.get("source", "harness-json")
        if not isinstance(complete, bool):
            raise ValueError("public token usage complete must be a bool")
        if not isinstance(source, str):
            raise ValueError("public token usage source must be a string")
        return cls(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=optional_counts["cache_read_tokens"],
            cache_write_tokens=optional_counts["cache_write_tokens"],
            reasoning_tokens=optional_counts["reasoning_tokens"],
            total_tokens=optional_counts["total_tokens"],
            cost_usd=cost_usd,
            complete=complete,
            source=source,
        )


@dataclass(frozen=True, slots=True)
class ParsedOutput:
    answer: bytes
    usage: TokenUsage | None
    diagnostic: str | None = None


def _sum_optional(left: int | float | None, right: int | float | None) -> Any:
    if left is None and right is None:
        return None
    return (left or 0) + (right or 0)


def json_lines(raw: bytes) -> list[dict[str, Any]]:
    """Return JSON object lines, ignoring blank, malformed, and non-object lines."""
    values: list[dict[str, Any]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError, UnicodeDecodeError:
            continue
        if isinstance(value, dict):
            values.append(value)
    return values


def json_document(raw: bytes) -> Any | None:
    """Parse a whole-buffer JSON document, returning None for invalid JSON."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError, UnicodeDecodeError:
        return None


def as_int(value: Any) -> int | None:
    """Return a non-negative integer, excluding bools despite their int subtype."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value
