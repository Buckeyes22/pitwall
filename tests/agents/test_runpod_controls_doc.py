"""The RunPod operator guide must name the serve fields, errors, and commands the code has."""

from __future__ import annotations

import re
from pathlib import Path

from pitwall.api.exceptions import LeaseNotServing
from pitwall.api.schemas.serve import ServeResponse
from pitwall.cli.leases import _parse_leases_args
from pitwall.mcp.registry import TOOL_NAMES

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "docs" / "operator" / "runpod-resource-controls.md"


def _code_spans() -> set[str]:
    return set(re.findall(r"`([^`\n]+)`", GUIDE.read_text(encoding="utf-8")))


def test_replay_fields_the_guide_quotes_exist_on_the_serve_response() -> None:
    assert "created: false" in _code_spans()
    assert "created" in ServeResponse.model_fields


def test_the_stop_paths_the_guide_names_exist() -> None:
    spans = _code_spans()

    assert "pitwall_stop_lease" in spans
    assert "pitwall_stop_lease" in TOOL_NAMES
    (command,) = [span for span in spans if span.startswith("pitwall leases stop")]
    args = _parse_leases_args(command.split()[2:])
    assert args.command == "stop"


def test_the_refusal_code_the_guide_names_is_the_one_the_api_raises() -> None:
    assert "lease_not_serving" in _code_spans()
    assert LeaseNotServing.reason == "lease_not_serving"


def _flat() -> str:
    return re.sub(r"\s+", " ", GUIDE.read_text(encoding="utf-8"))


def test_the_guide_does_not_promise_that_a_new_serve_key_forces_a_new_pod() -> None:
    # Guards the promise that a fresh idempotency key alone never replaces a serving pod: serve
    # finds the active lease and returns it with `created: false`.
    text = _flat()

    assert "A new key always launches a new pod" not in text
    assert "A new key alone does not replace a serving pod" in text
