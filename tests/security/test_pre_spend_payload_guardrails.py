"""Property coverage for pre-spend payload guardrail redaction."""

from __future__ import annotations

import re

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from pitwall.audit import checks
from pitwall.security.pre_spend import PreSpendInspectionService

pytestmark = [pytest.mark.security, pytest.mark.property]


_SAFE_TEXT = st.text(
    alphabet=st.characters(
        blacklist_categories=("Cs",),
        blacklist_characters=["@", "\x00"],
    ),
    max_size=32,
)


def _allowed_alone(text: str) -> bool:
    """Text the guardrail passes on its own; generated text can look like a secret (hf_...)."""
    result = PreSpendInspectionService(thread_time_ns=lambda: 0).preview(
        {"messages": [{"content": text}]}
    )
    return result.decision == checks.PreSpendDecision.ALLOW


@given(prefix=_SAFE_TEXT, suffix=_SAFE_TEXT)
def test_email_redaction_never_leaves_original_email_in_result(
    prefix: str,
    suffix: str,
) -> None:
    assume(_allowed_alone(prefix) and _allowed_alone(suffix))
    email = "ada.lovelace@example.com"
    payload = {"messages": [{"content": f"{prefix} {email} {suffix}"}]}

    result = PreSpendInspectionService(thread_time_ns=lambda: 0).preview(payload)

    assert result.decision == checks.PreSpendDecision.REDACT
    assert email not in str(result.to_dict())
    assert email not in str(result.redacted_payload)
    assert result.redacted_payload == {
        "messages": [{"content": f"{prefix} [REDACTED:email] {suffix}"}]
    }


@given(token=st.from_regex(re.compile(r"sk-test_[A-Za-z0-9]{32}"), fullmatch=True))
def test_secret_redaction_blocks_and_never_returns_original_token(token: str) -> None:
    result = PreSpendInspectionService(thread_time_ns=lambda: 0).preview(
        {"input": f"token={token}"}
    )

    assert result.decision == checks.PreSpendDecision.BLOCK
    assert result.blocked is True
    assert token not in str(result.to_dict())
    assert token not in str(result.redacted_payload)
    assert result.redacted_payload == {"input": "token=[REDACTED:secret]"}


def test_email_beside_a_secret_blocks_and_returns_neither() -> None:
    """Hypothesis found this: a token-shaped suffix makes the payload a secret, which blocks."""
    email = "ada.lovelace@example.com"
    token = "hf_0000000000000000"
    payload = {"messages": [{"content": f" {email} {token}"}]}

    result = PreSpendInspectionService(thread_time_ns=lambda: 0).preview(payload)

    assert result.decision == checks.PreSpendDecision.BLOCK
    assert result.blocked is True
    for value in (email, token):
        assert value not in str(result.to_dict())
        assert value not in str(result.redacted_payload)
