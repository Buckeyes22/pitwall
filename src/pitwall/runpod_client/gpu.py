"""RunPod GPU name validation and VRAM catalog.

The canonical names and VRAM values are from the RunPod gpuTypes snapshot
2026-08-27. Short aliases are rejected so callers do not silently launch in a
different capacity lane; the five historical full-name aliases are normalized
to their live ids for existing operator configuration compatibility.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType

_GPU_TYPES: tuple[tuple[str, int], ...] = (
    ("AMD Instinct MI300X OAM", 192),
    ("NVIDIA A100 80GB PCIe", 80),
    ("NVIDIA A100-SXM4-40GB", 40),
    ("NVIDIA A100-SXM4-80GB", 80),
    ("NVIDIA A40", 48),
    ("NVIDIA B200", 180),
    ("NVIDIA B300 SXM6 AC", 288),
    ("NVIDIA GeForce RTX 3070", 8),
    ("NVIDIA GeForce RTX 3080", 10),
    ("NVIDIA GeForce RTX 3080 Ti", 12),
    ("NVIDIA GeForce RTX 3090", 24),
    ("NVIDIA GeForce RTX 3090 Ti", 24),
    ("NVIDIA GeForce RTX 4070 Ti", 12),
    ("NVIDIA GeForce RTX 4080", 16),
    ("NVIDIA GeForce RTX 4080 SUPER", 16),
    ("NVIDIA GeForce RTX 4090", 24),
    ("NVIDIA GeForce RTX 5080", 16),
    ("NVIDIA GeForce RTX 5090", 32),
    ("NVIDIA H100 80GB HBM3", 80),
    ("NVIDIA H100 NVL", 94),
    ("NVIDIA H100 PCIe", 80),
    ("NVIDIA H200", 141),
    ("NVIDIA H200 NVL", 143),
    ("NVIDIA L4", 24),
    ("NVIDIA L40", 48),
    ("NVIDIA L40S", 48),
    ("NVIDIA RTX 2000 Ada Generation", 16),
    ("NVIDIA RTX 4000 Ada Generation", 20),
    ("NVIDIA RTX 4000 SFF Ada Generation", 20),
    ("NVIDIA RTX 5000 Ada Generation", 32),
    ("NVIDIA RTX 6000 Ada Generation", 48),
    ("NVIDIA RTX A2000", 6),
    ("NVIDIA RTX A4000", 16),
    ("NVIDIA RTX A4500", 20),
    ("NVIDIA RTX A5000", 24),
    ("NVIDIA RTX A6000", 48),
    ("NVIDIA RTX PRO 4000 Blackwell", 24),
    ("NVIDIA RTX PRO 4500 Blackwell", 32),
    ("NVIDIA RTX PRO 4500 Blackwell Server Edition", 32),
    ("NVIDIA RTX PRO 5000 Blackwell", 48),
    ("NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition", 96),
    ("NVIDIA RTX PRO 6000 Blackwell Server Edition", 96),
    ("NVIDIA RTX PRO 6000 Blackwell Server Edition MIG 1g.24gb", 24),
    ("NVIDIA RTX PRO 6000 Blackwell Server Edition MIG 2g.48gb", 48),
    ("NVIDIA RTX PRO 6000 Blackwell Workstation Edition", 96),
    ("Tesla V100-PCIE-16GB", 16),
    ("Tesla V100-SXM2-16GB", 16),
)

_CANONICAL_GPU_NAMES = tuple(gpu_name for gpu_name, _ in _GPU_TYPES)
CANONICAL_GPU_NAMES = frozenset(_CANONICAL_GPU_NAMES)
CANONICAL_RUNPOD_GPU_NAMES = CANONICAL_GPU_NAMES
CANONICAL_GPU_NAME_MAP: Mapping[str, str] = MappingProxyType(
    {gpu_name: gpu_name for gpu_name in _CANONICAL_GPU_NAMES}
)
GPU_VRAM_GB: Mapping[str, int] = MappingProxyType(dict(_GPU_TYPES))
LEGACY_GPU_NAME_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        "NVIDIA A100 80GB": "NVIDIA A100-SXM4-80GB",
        "NVIDIA A100 40GB": "NVIDIA A100-SXM4-40GB",
        "NVIDIA A6000": "NVIDIA RTX A6000",
        "NVIDIA RTX 6000 Ada": "NVIDIA RTX 6000 Ada Generation",
        "NVIDIA RTX 4090": "NVIDIA GeForce RTX 4090",
    }
)


def _lookup_key(value: str) -> str:
    return "".join(ch for ch in value.upper() if ch.isalnum())


_CANONICAL_GPU_NAME_BY_KEY: Mapping[str, str] = MappingProxyType(
    {_lookup_key(gpu_name): gpu_name for gpu_name in _CANONICAL_GPU_NAMES}
)
_SHORTHAND_GPU_SUGGESTIONS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "H100": ("NVIDIA H100 80GB HBM3", "NVIDIA H100 NVL", "NVIDIA H100 PCIe"),
        "H100PCIE": ("NVIDIA H100 PCIe",),
        "H200": ("NVIDIA H200", "NVIDIA H200 NVL"),
        "B200": ("NVIDIA B200",),
        "B300": ("NVIDIA B300 SXM6 AC",),
        "A100": ("NVIDIA A100-SXM4-80GB", "NVIDIA A100 80GB PCIe", "NVIDIA A100-SXM4-40GB"),
        "A6000": ("NVIDIA RTX A6000",),
        "A40": ("NVIDIA A40",),
        "L40": ("NVIDIA L40", "NVIDIA L40S"),
        "L40S": ("NVIDIA L40S",),
        "L4": ("NVIDIA L4",),
        "RTX6000ADA": ("NVIDIA RTX 6000 Ada Generation",),
        "RTX4090": ("NVIDIA GeForce RTX 4090",),
        "4090": ("NVIDIA GeForce RTX 4090",),
        "RTX5090": ("NVIDIA GeForce RTX 5090",),
        "5090": ("NVIDIA GeForce RTX 5090",),
        "RTXPRO6000": (
            "NVIDIA RTX PRO 6000 Blackwell Server Edition",
            "NVIDIA RTX PRO 6000 Blackwell Workstation Edition",
        ),
        "RTXA5000": ("NVIDIA RTX A5000",),
        "RTXA4500": ("NVIDIA RTX A4500",),
        "RTXA4000": ("NVIDIA RTX A4000",),
        "RTX5000ADA": ("NVIDIA RTX 5000 Ada Generation",),
        "RTX4000ADA": ("NVIDIA RTX 4000 Ada Generation",),
        "3090": ("NVIDIA GeForce RTX 3090", "NVIDIA GeForce RTX 3090 Ti"),
        "MI300X": ("AMD Instinct MI300X OAM",),
    }
)


class NonCanonicalGPUNameError(ValueError):
    """Raised when a RunPod GPU name is not canonical or a legacy full-name alias."""

    def __init__(self, gpu_name: str, suggestions: Iterable[str] = ()) -> None:
        self.gpu_name = gpu_name
        self.suggestions = tuple(suggestions)
        message = f"RunPod GPU name {gpu_name!r} is not canonical; use the exact RunPod gpuTypeId full name"
        if self.suggestions:
            message = f"{message}. Suggested canonical name(s): {', '.join(self.suggestions)}"
        else:
            message = f"{message}. Shorthand GPU names are rejected."
        super().__init__(message)


def is_canonical_gpu_name(gpu_name: str) -> bool:
    """Return true only for exact live RunPod GPU ids."""
    return gpu_name in CANONICAL_GPU_NAMES


def non_canonical_gpu_names(gpu_names: Iterable[str]) -> list[str]:
    """Return names that are not exact live RunPod GPU ids."""
    return [gpu_name for gpu_name in gpu_names if not is_canonical_gpu_name(gpu_name)]


def canonical_gpu_name_suggestions(gpu_name: str) -> tuple[str, ...]:
    """Return live-id diagnostic suggestions without accepting short aliases."""
    key = _lookup_key(gpu_name)
    if exact_case_suggestion := _CANONICAL_GPU_NAME_BY_KEY.get(key):
        return (exact_case_suggestion,)
    return _SHORTHAND_GPU_SUGGESTIONS.get(key, ())


def validate_canonical_gpu_name(gpu_name: str) -> str:
    """Validate a live id or legacy full-name alias and return its live id."""
    if is_canonical_gpu_name(gpu_name):
        return gpu_name
    if live_id := LEGACY_GPU_NAME_ALIASES.get(gpu_name):
        return live_id
    raise NonCanonicalGPUNameError(gpu_name, canonical_gpu_name_suggestions(gpu_name))


def validate_canonical_gpu_names(gpu_names: Iterable[str]) -> list[str]:
    """Validate names and return live RunPod GPU ids in input order."""
    return [validate_canonical_gpu_name(gpu_name) for gpu_name in gpu_names]


validate_gpu_type = validate_canonical_gpu_name
validate_gpu_types = validate_canonical_gpu_names


__all__ = [
    "CANONICAL_GPU_NAME_MAP",
    "CANONICAL_GPU_NAMES",
    "CANONICAL_RUNPOD_GPU_NAMES",
    "GPU_VRAM_GB",
    "LEGACY_GPU_NAME_ALIASES",
    "NonCanonicalGPUNameError",
    "canonical_gpu_name_suggestions",
    "is_canonical_gpu_name",
    "non_canonical_gpu_names",
    "validate_canonical_gpu_name",
    "validate_canonical_gpu_names",
    "validate_gpu_type",
    "validate_gpu_types",
]
