"""Harness adapters for the public transport shims."""

from __future__ import annotations

from .agy import AgyAdapter
from .base import HarnessAdapter
from .claude import ClaudeAdapter
from .cline import ClineAdapter
from .codex import CodexAdapter
from .dsh import DshAdapter
from .goose import GooseAdapter
from .grok import GrokAdapter
from .hermes import HermesAdapter
from .kimi import KimiAdapter
from .muse import MuseAdapter
from .opencode import OpenCodeAdapter
from .pi import PiAdapter
from .qwen import QwenAdapter
from .zcode import ZCodeAdapter

_ADAPTERS: dict[str, type[HarnessAdapter]] = {
    "agy": AgyAdapter,
    "claude": ClaudeAdapter,
    "cline": ClineAdapter,
    "codex": CodexAdapter,
    "dsh": DshAdapter,
    "grok": GrokAdapter,
    "goose": GooseAdapter,
    "hermes": HermesAdapter,
    "kimi": KimiAdapter,
    "muse": MuseAdapter,
    "opencode": OpenCodeAdapter,
    "pi": PiAdapter,
    "qwen": QwenAdapter,
    "zcode": ZCodeAdapter,
}


def get_adapter(harness_id: str) -> HarnessAdapter:
    try:
        return _ADAPTERS[harness_id]()
    except KeyError as exc:
        raise ValueError(f"unknown harness: {harness_id}") from exc


def adapter_ids() -> set[str]:
    return set(_ADAPTERS)
