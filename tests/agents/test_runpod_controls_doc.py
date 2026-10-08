"""The RunPod operator guide must match what serve does with a new idempotency key."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _flat(path: Path) -> str:
    return re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))


def test_runpod_controls_do_not_promise_that_a_new_serve_key_forces_a_new_pod() -> None:
    text = _flat(ROOT / "docs" / "operator" / "runpod-resource-controls.md")
    assert "A new key always launches a new pod" not in text
    assert "`created: false`" in text
    assert "`pitwall_stop_lease`" in text
    assert "A new key alone does not replace a serving pod" in text
