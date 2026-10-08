from __future__ import annotations

from decimal import Decimal

from pitwall.models.schema import Variant

BYTES_PER_GIB = Decimal(1024**3)
CONSERVATIVE_BYTES_PER_TOKEN = Decimal(256 * 1024)


def kv_cache_gb(variant: Variant, context_length: int) -> Decimal:
    """Estimate KV GiB; use architecture data when present, else 256 KiB/token."""
    if context_length < 1:
        raise ValueError("context_length must be positive")
    if variant.params_total_b is not None and variant.layers is not None:
        hidden = (variant.params_total_b * Decimal(1_000_000_000) / variant.layers).sqrt()
        bytes_total = Decimal(4) * hidden * variant.layers * context_length
    else:
        bytes_total = CONSERVATIVE_BYTES_PER_TOKEN * context_length
    return (bytes_total / BYTES_PER_GIB).quantize(Decimal("0.001"))


__all__ = ["kv_cache_gb"]
