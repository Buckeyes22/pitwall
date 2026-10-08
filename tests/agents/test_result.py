"""Dispatch-result semantic validation stays aligned with its JSON schema."""

from __future__ import annotations

import unittest
from pathlib import Path

from pitwall.agents.result import (
    ResultError,
    validate_result,
)

ROOT = Path(__file__).resolve().parents[2]


def valid_result() -> dict[str, object]:
    digest = {"bytes": 0, "sha256": "0" * 64}
    return {
        "schemaVersion": 1,
        "dispatchId": "00000000-0000-4000-8000-000000000001",
        "workflowId": None,
        "taskId": None,
        "provider": "codex",
        "model": "gpt-test",
        "requestedModel": "gpt-test",
        "effort": None,
        "arguments": [],
        "providerVersion": None,
        "status": "succeeded",
        "outcome": "ok",
        "createdAt": "2026-07-10T00:00:00.000Z",
        "startedAt": "2026-07-10T00:00:00.000Z",
        "finishedAt": "2026-07-10T00:00:01.000Z",
        "wallMs": 1000,
        "exitCode": 0,
        "signal": None,
        "timeout": {"seconds": 1140, "expired": False},
        "sentinel": {"emitted": True, "exit": 0},
        "workspace": {"mode": "shared", "path": "/tmp", "baseSha": None, "finalSha": None},
        "output": {"stdout": dict(digest), "stderr": dict(digest)},
        "artifacts": {"result": "/tmp/result.json"},
        "integration": {"status": "not_applied", "appliedAt": None, "target": None},
    }


class ResultTests(unittest.TestCase):
    def test_valid_result_is_accepted(self) -> None:
        validate_result(valid_result())

    def test_route_object_is_optional_but_shape_checked(self) -> None:
        value = valid_result()
        value["route"] = {
            "spec": "glimmer@qwen",
            "name": "glimmer",
            "harness": "qwen",
            "model": "m/x",
            "endpointHost": "gpu-1:8000",
        }
        validate_result(value)
        value["route"] = {"spec": "glimmer"}
        with self.assertRaisesRegex(ResultError, "route"):
            validate_result(value)
        value["route"] = {
            "spec": "g",
            "name": "g",
            "harness": "qwen",
            "model": "m",
            "endpointHost": 8000,
        }
        with self.assertRaisesRegex(ResultError, "endpointHost"):
            validate_result(value)

    def test_signal_and_arguments_types_are_enforced(self) -> None:
        for field, invalid in (("signal", "SIGTERM"), ("arguments", "--model gpt-test")):
            with self.subTest(field=field):
                value = valid_result()
                value[field] = invalid
                with self.assertRaisesRegex(ResultError, field):
                    validate_result(value)


if __name__ == "__main__":
    unittest.main()
