"""CLI contracts for guardrail status and non-persisting JSON preview."""

from __future__ import annotations

import json

from pitwall.cli.guardrails import cmd_guardrails
from pitwall.security.pre_spend import PreSpendInspectionLimits, PreSpendInspectionService


def test_guardrail_status_json(capsys) -> None:
    service = PreSpendInspectionService()

    assert cmd_guardrails(["status", "--json"], service=service) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "balanced"
    assert payload["counters"]["total"] == 0
    assert payload["rules"]


def test_guardrail_preview_json_is_non_persisting_and_non_disclosing(capsys) -> None:
    service = PreSpendInspectionService()
    secret = "sk-abcdefghijklmnop12345678"

    assert (
        cmd_guardrails(
            ["preview", "--payload", json.dumps({"token": secret}), "--json"],
            service=service,
        )
        == 0
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["decision"] == "block"
    assert payload["findings"][0]["rule"] == "secret_field"
    assert secret not in captured.out
    assert service.status().counters.total == 0


def test_guardrail_preview_invalid_json_is_safe(capsys) -> None:
    secret = "sk-abcdefghijklmnop12345678"

    assert cmd_guardrails(["preview", "--payload", "{" + secret]) == 2

    captured = capsys.readouterr()
    assert "payload must be valid JSON" in captured.err
    assert secret not in captured.err


def test_guardrail_preview_bounds_json_before_parsing(capsys) -> None:
    service = PreSpendInspectionService(limits=PreSpendInspectionLimits(max_input_bytes=16))

    assert (
        cmd_guardrails(
            ["preview", "--payload", '{"value":"abcdefghijklmnop"}'],
            service=service,
        )
        == 2
    )

    captured = capsys.readouterr()
    assert "exceeds the configured guardrail input limit" in captured.err
    assert "abcdefghijklmnop" not in captured.err
    assert service.status().counters.total == 0
