"""BackgroundAction reports what its helper thread did instead of failing silently."""

from __future__ import annotations

import pytest

from tests.hang_guard import BackgroundAction


def test_an_error_on_the_helper_thread_is_raised_by_finish() -> None:
    def poll() -> bool:
        raise FileNotFoundError("events.jsonl")

    action = BackgroundAction(poll, interval=0.01)
    with pytest.raises(AssertionError, match="background action failed: FileNotFoundError"):
        action.finish()


def test_finish_fails_when_the_condition_was_never_seen() -> None:
    action = BackgroundAction(lambda: False, interval=0.01)
    with pytest.raises(AssertionError, match="never saw its condition"):
        action.finish()


def test_an_action_that_ran_finishes_cleanly() -> None:
    action = BackgroundAction(lambda: True, interval=0.01)
    action.finish()
    assert action.acted
