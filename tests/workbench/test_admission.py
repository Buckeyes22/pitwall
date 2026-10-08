"""Admission tests, translated from admission, shared-admission, and runtime-admission tests."""

from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
import sys
import threading
import time
import types
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pytest

from pitwall.workbench import admission as admission_module
from pitwall.workbench.admission import RequestAdmission, RequestCancelled, SharedRequestAdmission
from pitwall.workbench.launcher import PiLaunchOptions, extension_path, launch_pi
from pitwall.workbench.profile import compile_profile, configure_provider_profile
from tests.hang_guard import HANG_GUARD_SECS
from tests.workbench.pi_support import (
    FixtureServer,
    RpcClient,
    chat_chunk,
    chat_usage,
    pinned_pi,
    requires_pi,
    sse_headers,
    sse_write,
)


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.mark.parity
async def test_hands_a_released_request_slot_to_its_waiter_without_allowing_a_new_request_to_overtake() -> (
    None
):
    """Source: admission.test.ts 'hands a released request slot to its waiter without allowing a new request to overtake'."""
    gate = RequestAdmission()
    first = await gate.acquire()
    order: list[str] = []

    async def take(label: str) -> object:
        release = await gate.acquire()
        order.append(label)
        return release

    second = asyncio.create_task(take("second"))
    await settle()
    first()
    first()  # releasing twice must not free a second permit
    third = asyncio.create_task(take("third"))
    release_second = await second
    assert order == ["second"]
    assert callable(release_second)
    release_second()
    release_third = await third
    assert order == ["second", "third"]
    assert callable(release_third)
    release_third()


@pytest.mark.parity
async def test_cancelled_waiters_never_consume_a_slot_or_block_the_next_request() -> None:
    """Source: admission.test.ts 'cancelled waiters never consume a slot or block the next request'."""
    gate = RequestAdmission()
    release = await gate.acquire()
    cancelled = asyncio.create_task(gate.acquire())
    await settle()
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    following = asyncio.create_task(gate.acquire())
    await settle()
    release()
    (await following)()
    (await gate.acquire())()


async def test_a_waiter_cancelled_after_receiving_the_permit_passes_it_on() -> None:
    gate = RequestAdmission()
    release = await gate.acquire()
    doomed = asyncio.create_task(gate.acquire())
    survivor = asyncio.create_task(gate.acquire())
    await settle()
    release()  # the permit is handed to `doomed`, which is cancelled in the same tick
    doomed.cancel()
    with pytest.raises(asyncio.CancelledError):
        await doomed
    (await asyncio.wait_for(survivor, 1))()


def wait_until(check: object, timeout: float = HANG_GUARD_SECS) -> None:
    assert callable(check)
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "admission fixture deadline"
        time.sleep(0.02)


class PollProbe:
    """Count the times a queued ``acquire`` found the host lock held and slept before retrying.

    A queued request is observably waiting once it has polled, so tests hold their negative
    assertions until it has (twice, to show it keeps polling) instead of sleeping and hoping.
    """

    def __init__(self) -> None:
        self.polls = 0
        self._changed = threading.Condition()

    def sleep(self, seconds: float) -> None:
        with self._changed:
            self.polls += 1
            self._changed.notify_all()
        time.sleep(seconds)

    def wait_for_more_polls(self, count: int = 2) -> None:
        with self._changed:
            target = self.polls + count
            assert self._changed.wait_for(lambda: self.polls >= target, HANG_GUARD_SECS), (
                "the queued request never polled the held lock"
            )


@pytest.fixture
def poll_probe(monkeypatch: pytest.MonkeyPatch) -> PollProbe:
    probe = PollProbe()
    monkeypatch.setattr(admission_module, "time", types.SimpleNamespace(sleep=probe.sleep))
    return probe


def join_thread(thread: threading.Thread) -> None:
    thread.join(HANG_GUARD_SECS)
    assert not thread.is_alive(), "the acquiring thread is still running after the hang guard"


def acquire_in_thread(
    admission: SharedRequestAdmission, cancel: threading.Event | None = None
) -> tuple[threading.Thread, dict[str, object]]:
    outcome: dict[str, object] = {}

    def run() -> None:
        try:
            outcome["release"] = admission.acquire(cancel)
        except RequestCancelled as error:
            outcome["error"] = error

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, outcome


@pytest.mark.parity
def test_shared_host_lock_serializes_independent_instances_and_cancels_a_queued_request(
    tmp_path: Path,
    poll_probe: PollProbe,
) -> None:
    """Source: shared-admission.test.ts 'shared host lock serializes independent instances and cancels a queued request'."""
    first = SharedRequestAdmission("same-endpoint", tmp_path)
    second = SharedRequestAdmission("same-endpoint", tmp_path)
    release = first.acquire()
    cancel = threading.Event()
    thread, outcome = acquire_in_thread(second, cancel)
    poll_probe.wait_for_more_polls()
    cancel.set()
    join_thread(thread)
    assert str(outcome["error"]) == "Request cancelled"
    entering, entered = acquire_in_thread(second)
    poll_probe.wait_for_more_polls()
    assert "release" not in entered
    release()
    release()  # idempotent
    wait_until(lambda: "release" in entered)
    join_thread(entering)
    next_release = entered["release"]
    assert callable(next_release)
    next_release()


@pytest.mark.parity
def test_abort_after_grant_does_not_release_the_permit_before_transport_settlement(
    tmp_path: Path,
    poll_probe: PollProbe,
) -> None:
    """Source: shared-admission.test.ts 'abort after grant does not release the permit before transport settlement'."""
    cancel = threading.Event()
    release = SharedRequestAdmission("same-endpoint", tmp_path).acquire(cancel)
    cancel.set()
    waiting, entered = acquire_in_thread(SharedRequestAdmission("same-endpoint", tmp_path))
    poll_probe.wait_for_more_polls()
    assert "release" not in entered
    release()
    wait_until(lambda: "release" in entered)
    join_thread(waiting)
    next_release = entered["release"]
    assert callable(next_release)
    next_release()


HOLDER = """
import sys, time
from pathlib import Path
from pitwall.workbench.admission import SharedRequestAdmission
SharedRequestAdmission("endpoint", sys.argv[1]).acquire()
Path(sys.argv[2]).write_text("held")
time.sleep(60)
"""


@pytest.mark.parity
def test_two_actual_host_processes_share_the_permit_and_a_killed_host_releases_ownership(
    tmp_path: Path,
    poll_probe: PollProbe,
) -> None:
    """Source: shared-admission.test.ts 'two actual host processes share the permit and a killed host releases ownership'."""
    marker = tmp_path / "acquired"
    host = subprocess.Popen([sys.executable, "-c", HOLDER, str(tmp_path), str(marker)])
    try:
        wait_until(lambda: marker.exists() and marker.read_text() == "held")
        waiting, entered = acquire_in_thread(SharedRequestAdmission("endpoint", tmp_path))
        poll_probe.wait_for_more_polls()
        assert "release" not in entered
        host.kill()
        wait_until(lambda: "release" in entered)
        join_thread(waiting)
        next_release = entered["release"]
        assert callable(next_release)
        next_release()
    finally:
        host.kill()
        host.wait()


def test_shared_admission_rejects_an_invalid_group_and_an_unsafe_directory(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="invalid admission resource group"):
        SharedRequestAdmission("  ", tmp_path)
    with pytest.raises(ValueError, match="invalid admission resource group"):
        SharedRequestAdmission("x" * 201, tmp_path)
    broad = tmp_path / "broad"
    broad.mkdir(mode=0o755)
    broad.chmod(0o755)
    with pytest.raises(PermissionError, match="private and owned"):
        SharedRequestAdmission("group", broad).acquire()
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(PermissionError, match="private and owned"):
        SharedRequestAdmission("group", link).acquire()


def flock_waiter(parent_pid: int, lock_path: Path) -> bool:
    """True when ``parent_pid`` has a child ``flock`` waiting on ``lock_path``."""
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            command = [
                part
                for part in Path(f"/proc/{entry}/cmdline").read_bytes().decode().split("\0")
                if part
            ]
            if not command or Path(command[0]).name != "flock":
                continue
            if command[1:4] != ["--exclusive", "--no-fork", str(lock_path)]:
                continue
            stat = Path(f"/proc/{entry}/stat").read_text()
            after_command = stat[stat.rindex(") ") + 2 :].split()
            if int(after_command[1]) == parent_pid:
                return True
        except OSError, ValueError:
            continue  # the process exited while it was being inspected
    return False


@pytest.mark.live
@pytest.mark.parity
@requires_pi
def test_actual_pi_runtime_cancels_a_request_waiting_for_shared_admission_without_a_late_transport_request(
    tmp_path: Path,
) -> None:
    """Source: runtime-admission.test.ts 'actual Pi runtime cancels a request waiting for shared admission without a late transport request'."""
    resource_directory = tmp_path / "admission"
    requests = 0
    first_release = threading.Event()
    counter = threading.Lock()

    def respond(handler: BaseHTTPRequestHandler, path: str, body: bytes) -> None:
        nonlocal requests
        with counter:
            requests += 1
            number = requests
        sse_headers(handler)
        if number == 1:
            first_release.wait(30)
            text = "first complete"
        else:
            text = "third complete"
        sse_write(handler, chat_chunk({"role": "assistant", "content": text}, "stop"))
        sse_write(handler, chat_usage(2, 2))
        sse_write(handler, "data: [DONE]\n\n")

    clients: list[RpcClient] = []
    with FixtureServer(respond) as server:
        try:
            for name in ("first", "second"):
                root = tmp_path / name
                root.mkdir()
                profile = {
                    "provider": "fixture",
                    "modelId": "fixture-model",
                    "endpoint": server.url,
                    "api": "openai-completions",
                    "keyless": "dummy",
                    "servedContextTokens": 32768,
                    "maxCompletionTokens": 4096,
                    "resourceGroup": "shared-admission-fixture",
                    "allowProviderFallback": False,
                }
                compiled = compile_profile("fixture", profile, root / "agent")
                provider = configure_provider_profile(compiled, root)
                client = RpcClient(
                    launch_pi(
                        PiLaunchOptions(
                            cwd=root,
                            profile=compiled,
                            pi_bin=pinned_pi(),
                            env={
                                **provider.env,
                                "PITWALL_WORKBENCH_RESOURCE_DIR": str(resource_directory),
                            },
                            extension=extension_path("extension"),
                            runtime_dir=tmp_path / "runtime",
                        )
                    )
                )
                clients.append(client)
                client.command("get_state")
                client.command("set_auto_retry", enabled=False)
            first, second = clients
            first.send(
                {
                    "type": "prompt",
                    "id": "first-prompt",
                    "message": "hold the first provider request",
                }
            )
            first.until(lambda: requests == 1, what="first provider request")
            second.send(
                {
                    "type": "prompt",
                    "id": "second-prompt",
                    "message": "this request must wait for admission",
                }
            )
            lock_path = resource_directory / (
                hashlib.sha256(b"shared-admission-fixture").hexdigest() + ".lock"
            )
            second.until(lambda: flock_waiter(second.child.pid, lock_path), what="admission waiter")
            assert requests == 1
            assert second.command("get_state")["data"]["isStreaming"] is True
            second.command("abort")
            second.until(lambda: second.settled_count() >= 1, what="aborted request settling")
            first_release.set()
            first.until(lambda: first.settled_count() >= 1, what="first request settling")
            settled_before = second.settled_count()
            second.send(
                {
                    "type": "prompt",
                    "id": "third-prompt",
                    "message": "prove the released permit is reusable",
                }
            )
            second.until(lambda: second.settled_count() > settled_before, what="third request")
            assert requests == 2
            state = second.command("get_state")["data"]
            assert state["isStreaming"] is False
            assert state["pendingMessageCount"] == 0
        finally:
            first_release.set()
            for client in clients:
                client.close()
