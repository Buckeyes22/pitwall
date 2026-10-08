"""D3 eligibility and the endpoint-route answerer (plan Task 25)."""

from __future__ import annotations

import json
import os
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from pitwall.agents.channel_policy import (
    POLICY_MAX_TOKENS,
    escalation_reason,
    request_choice,
)

ROOT = Path(__file__).resolve().parents[2]


def _ask(**overrides: object) -> dict:
    ask = {
        "ask_id": "0001",
        "blocked_on": "naming",
        "severity": "normal",
        "question": "Which suffix?",
        "default": "a",
        "default_rationale": "conventional",
        "context": {
            "options": [{"id": "a", "text": "-alpha"}, {"id": "b", "text": "-beta"}],
            "files_touched": ["db/x.sql"],
        },
    }
    ask.update(overrides)
    return ask


class PolicyStub:
    def __init__(self) -> None:
        self.reply = '{"choice": "b"}'
        self.status = 200
        self.delay = 0.0
        self.finish_reason: str | None = None
        self.requests: list[tuple[dict, dict]] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802  # reason: method name is dictated by the standard-library interface
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                stub.requests.append((dict(self.headers), body))
                time.sleep(stub.delay)
                choice = {"message": {"role": "assistant", "content": stub.reply}}
                if stub.finish_reason is not None:
                    choice["finish_reason"] = stub.finish_reason
                payload = json.dumps({"choices": [choice]}).encode()
                try:
                    self.send_response(stub.status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except BrokenPipeError:
                    pass  # the client gave up during stub.delay; that is the case under test

            def log_message(self, *_args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.entry = {
            "model": "stub-model",
            "endpoint": {
                "baseUrl": f"http://127.0.0.1:{self.server.server_port}/v1",
                "apiKeyEnv": "STUB_KEY",
            },
        }

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class EligibilityTests(unittest.TestCase):
    def test_d3_table(self) -> None:
        self.assertIsNone(escalation_reason(_ask()))
        for blocked in ("schema", "destructive", "spend"):
            self.assertIn("escalates", escalation_reason(_ask(blocked_on=blocked)) or "")
        self.assertIn("blocking", escalation_reason(_ask(severity="blocking")) or "")
        self.assertIn("abort", escalation_reason(_ask(default="abort")) or "")
        self.assertIn(
            "two options",
            escalation_reason(_ask(context={"options": [{"id": "a", "text": "x"}]})) or "",
        )


@mock.patch.dict(os.environ, {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
class AnswererTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stub = PolicyStub()
        self.env = {"STUB_KEY": "stub-secret"}

    def tearDown(self) -> None:
        self.stub.close()

    def _decide(self, **kwargs: object):  # type: ignore[no-untyped-def]  # reason: test helper is intentionally left unannotated
        return request_choice(
            _ask(**kwargs),
            route_name="policy-stub",
            entry=self.stub.entry,
            env=self.env,
            timeout=1.0,
        )

    def test_selects_an_option_with_provenance_and_pointer_context(self) -> None:
        decision = self._decide()
        self.assertEqual(("b", "policy:stub-model"), (decision.choice, decision.answered_by))
        headers, body = self.stub.requests[0]
        self.assertEqual("Bearer stub-secret", headers["Authorization"])
        self.assertEqual(("stub-model", 0), (body["model"], body["temperature"]))
        user = body["messages"][-1]["content"]
        self.assertIn("b: -beta", user)
        self.assertIn("db/x.sql", user)

    def test_prose_wrapped_json_is_accepted(self) -> None:
        self.stub.reply = 'Sure. {"choice": "a"}'
        self.assertEqual("a", self._decide().choice)

    def test_anything_but_a_provided_option_escalates(self) -> None:
        for reply in ('{"choice": "abort"}', '{"choice": "zzz"}', "pick b", ""):
            with self.subTest(reply=reply):
                self.stub.reply = reply
                decision = self._decide()
                self.assertIsNone(decision.choice)
                self.assertIn("did not select", decision.reason)

    def test_transport_failures_escalate(self) -> None:
        self.stub.status = 500
        self.assertIn("HTTP 500", self._decide().reason)
        self.stub.status, self.stub.delay = 200, 2.0
        self.assertIn("failed", self._decide().reason)

    def test_the_token_budget_leaves_room_for_reasoning(self) -> None:
        self._decide()
        self.assertEqual(POLICY_MAX_TOKENS, self.stub.requests[-1][1]["max_tokens"])
        self.assertGreaterEqual(POLICY_MAX_TOKENS, 1024)
        limited = {**self.stub.entry, "limits": {"output": 8192}}
        request_choice(_ask(), route_name="policy-stub", entry=limited, env=self.env, timeout=1.0)
        self.assertEqual(8192, self.stub.requests[-1][1]["max_tokens"])

    def test_a_reply_cut_off_by_the_token_limit_says_so(self) -> None:
        self.stub.reply, self.stub.finish_reason = None, "length"
        decision = self._decide()
        self.assertIsNone(decision.choice)
        self.assertIn("token limit", decision.reason)

    def test_routes_without_an_endpoint_cannot_answer(self) -> None:
        decision = request_choice(
            _ask(), route_name="plain", entry={"model": "m"}, env={}, timeout=1.0
        )
        self.assertIn("has no endpoint", decision.reason)


if __name__ == "__main__":
    unittest.main()
