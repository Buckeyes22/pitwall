"""Widen the mailbox's open-ask critical section with a handshake instead of a timer."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest import mock

from pitwall.agents import mailbox as mailbox_module
from tests.hang_guard import HANG_GUARD_SECS


class OpenAskContention:
    """Count callers that asked for the open-ask lock and let the first wait for the second.

    ``hold()`` is called from inside a caller's open-ask lookup, which runs under that lock when
    the mailbox is correct. It returns once a second caller has asked for the same lock, so the
    check-then-write window stays open exactly until the other writer has had its chance to race
    through. A mailbox without the lock lets both through; one with it queues the second.
    """

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._attempts = 0
        self.contended = threading.Event()

    @contextmanager
    def installed(self) -> Iterator[None]:
        original = mailbox_module._locked_file

        def observed(path: Path, *args: Any, **kwargs: Any) -> Any:
            if path.name == mailbox_module._OPEN_ASK_LOCK:
                with self._guard:
                    self._attempts += 1
                    if self._attempts >= 2:
                        self.contended.set()
            return original(path, *args, **kwargs)

        with mock.patch.object(mailbox_module, "_locked_file", observed):
            yield

    def hold(self) -> None:
        if not self.contended.wait(HANG_GUARD_SECS):
            raise AssertionError("no second caller asked for the open-ask lock")
