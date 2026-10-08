"""Contract tests for the swapper-faithful OpenAI loopback stub."""

from __future__ import annotations

import json
import threading
import time
import unittest
from concurrent.futures import Future, ThreadPoolExecutor
from http.client import HTTPResponse
from typing import Any
from urllib import error, request

from tests.agents.openai_stub import SwapperStub
from tests.hang_guard import HANG_GUARD_SECS


def _request(
    stub: SwapperStub,
    method: str,
    path: str,
    *,
    body: dict[str, object] | None = None,
    key: str | None = None,
) -> tuple[int, dict[str, Any]]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if key is not None:
        headers["Authorization"] = f"Bearer {key}"
    req = request.Request(stub.base_url + path, data=data, headers=headers, method=method)
    try:
        response: HTTPResponse | error.HTTPError = request.urlopen(req, timeout=HANG_GUARD_SECS)
    except error.HTTPError as exc:
        response = exc
    with response:
        status = response.status
        if status is None:
            raise AssertionError("stub response had no HTTP status")
        return status, json.loads(response.read().decode("utf-8"))


def _completion(
    stub: SwapperStub,
    model: str,
    *,
    key: str | None = None,
    content: str = "ping",
) -> tuple[int, dict[str, Any]]:
    return _request(
        stub,
        "POST",
        "/v1/chat/completions",
        body={"model": model, "messages": [{"role": "user", "content": content}]},
        key=key,
    )


class SwapperStubTests(unittest.TestCase):
    def test_catalog_is_instant_while_first_completion_blocks_then_stays_warm(self) -> None:
        # The cold load is held on a gate, so "instant" and "warm" are proven by ordering:
        # the catalog answers while the first completion is still blocked, and the second
        # completion starts no cold load. No wall-clock bound is involved.
        gate = threading.Event()
        with (
            SwapperStub({"alpha": {"first_request_gate": gate}}) as stub,
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            first_future = pool.submit(_completion, stub, "alpha", content="abcdefgh")
            self._wait_for_state(stub, "alpha", "starting", first_future)
            status, catalog = _request(stub, "GET", "/v1/models")
            self.assertFalse(first_future.done(), "the cold load did not block the completion")
            gate.set()
            status_first, first = first_future.result(timeout=HANG_GUARD_SECS)
            status_second, _second = _completion(stub, "alpha")

        self.assertEqual(200, status)
        self.assertEqual([{"id": "alpha", "object": "model"}], catalog["data"])
        self.assertEqual((200, 200), (status_first, status_second))
        self.assertEqual(["alpha"], stub.cold_loads)
        self.assertEqual("pong", first["choices"][0]["message"]["content"])
        self.assertEqual(2, first["usage"]["prompt_tokens"])

    def test_required_key_rejects_missing_or_mismatched_bearer(self) -> None:
        with SwapperStub({"alpha": {"require_key": "secret"}}) as stub:
            missing = _completion(stub, "alpha")
            wrong = _completion(stub, "alpha", key="wrong")
            accepted = _completion(stub, "alpha", key="secret")

        self.assertEqual((401, {"error": "unauthorized"}), missing)
        self.assertEqual((401, {"error": "unauthorized"}), wrong)
        self.assertEqual(200, accepted[0])

    def test_single_slot_evicts_the_previously_loaded_model(self) -> None:
        delay = 0.08
        models = {
            "alpha": {"first_request_delay": delay, "single_slot": True},
            "beta": {"first_request_delay": delay, "single_slot": True},
        }
        with SwapperStub(models) as stub:
            elapsed: list[float] = []
            for model in ("alpha", "beta", "alpha"):
                started = time.monotonic()
                self.assertEqual(200, _completion(stub, model)[0])
                elapsed.append(time.monotonic() - started)

        self.assertTrue(all(item >= 0.06 for item in elapsed), elapsed)

    def test_catalog_can_change_from_empty_to_populated_and_back(self) -> None:
        with SwapperStub({}) as stub:
            self.assertEqual([], _request(stub, "GET", "/v1/models")[1]["data"])
            stub.add_model("alpha", {"first_request_delay": 0})
            self.assertEqual(
                [{"id": "alpha", "object": "model"}],
                _request(stub, "GET", "/v1/models")[1]["data"],
            )
            stub.remove_model("alpha")
            self.assertEqual([], _request(stub, "GET", "/v1/models")[1]["data"])

    def test_running_reports_starting_then_ready_and_unload_clears_it(self) -> None:
        gate = threading.Event()
        with SwapperStub({"alpha": {"first_request_gate": gate}}) as stub:
            with ThreadPoolExecutor(max_workers=1) as pool:
                completion = pool.submit(_completion, stub, "alpha")
                running = self._wait_for_state(stub, "alpha", "starting", completion)
                self.assertEqual({"model": "alpha", "state": "starting"}, running)
                gate.set()
                self.assertEqual(200, completion.result(timeout=HANG_GUARD_SECS)[0])

            ready = _request(stub, "GET", "/running")[1]
            self.assertEqual([{"model": "alpha", "state": "ready"}], ready["running"])
            unloaded = _request(stub, "POST", "/api/models/unload/alpha")
            self.assertEqual(200, unloaded[0])
            self.assertEqual([], _request(stub, "GET", "/running")[1]["running"])

    def test_failed_start_is_starting_then_disappears_before_500(self) -> None:
        first_request_gate = threading.Event()
        fail_start_gate = threading.Event()
        with (
            SwapperStub(
                {
                    "broken": {
                        "first_request_gate": first_request_gate,
                        "fail_start": True,
                        "fail_start_gate": fail_start_gate,
                    }
                }
            ) as stub,
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            completion = pool.submit(_completion, stub, "broken")
            running = self._wait_for_state(stub, "broken", "starting", completion)
            self.assertEqual({"model": "broken", "state": "starting"}, running)
            first_request_gate.set()
            self._wait_until_absent(stub, "broken", completion)
            self.assertFalse(completion.done())
            fail_start_gate.set()
            status, payload = completion.result(timeout=HANG_GUARD_SECS)

        self.assertEqual(500, status)
        self.assertEqual({"error": "upstream command exited prematurely"}, payload)

    def _wait_for_state(
        self,
        stub: SwapperStub,
        model: str,
        state: str,
        completion: Future[tuple[int, dict[str, Any]]],
    ) -> dict[str, Any]:
        deadline = time.monotonic() + HANG_GUARD_SECS
        while time.monotonic() < deadline:
            entries = _request(stub, "GET", "/running")[1]["running"]
            for entry in entries:
                if entry == {"model": model, "state": state}:
                    return entry
            if completion.done():
                self.fail(f"completion finished before {model!r} entered {state!r}")
            time.sleep(0.005)
        self.fail(f"timed out waiting for {model!r} to enter {state!r}")

    def _wait_until_absent(
        self,
        stub: SwapperStub,
        model: str,
        completion: Future[tuple[int, dict[str, Any]]],
    ) -> None:
        deadline = time.monotonic() + HANG_GUARD_SECS
        while time.monotonic() < deadline:
            entries = _request(stub, "GET", "/running")[1]["running"]
            if not any(entry["model"] == model for entry in entries):
                return
            if completion.done():
                self.fail("completion returned before failed model disappeared from /running")
            time.sleep(0.005)
        self.fail(f"timed out waiting for {model!r} to disappear")


if __name__ == "__main__":
    unittest.main()
