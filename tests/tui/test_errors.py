"""Tests for the shared operator-console failure line."""

from __future__ import annotations

from pitwall.tui.errors import source_failure_message


def test_failure_line_keeps_the_reason_on_one_line() -> None:
    message = source_failure_message(
        "Overview unavailable", ConnectionRefusedError(111, "Connect call\nfailed")
    )

    assert message == "Overview unavailable: [Errno 111] Connect call failed"


def test_failure_line_redacts_credentials_in_the_reason() -> None:
    password = "-".join(("hunter2", "db", "pass"))
    dsn = f"postgresql://pitwall:{password}@db.example.invalid/pitwall"
    message = source_failure_message("Overview unavailable", RuntimeError(f"cannot reach {dsn}"))

    assert message.startswith("Overview unavailable: cannot reach postgresql://[REDACTED]@")
    assert password not in message


def test_failure_line_names_the_exception_when_it_has_no_text() -> None:
    assert source_failure_message("Cost unavailable", TimeoutError()) == (
        "Cost unavailable: TimeoutError"
    )
