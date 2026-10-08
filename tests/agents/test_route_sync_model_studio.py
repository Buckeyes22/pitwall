"""Harness sync writes Model Studio endpoints with protocol, usage, limits, and effort."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pitwall.agents import profiles
from pitwall.agents.harnesses import get_adapter
from pitwall.agents.registry import (
    load_registry,
)

ROOT = Path(__file__).resolve().parents[2]

REGISTRY = load_registry()


def entries(protocol: str = "openai", harness: str = "opencode") -> dict[str, object]:
    doc = {
        "schemaVersion": 1,
        "endpoints": {
            "ms": {
                "kind": "model-studio",
                "plan": "token-plan-personal",
                "tier": "pro",
                "apiKeyEnv": "MODEL_STUDIO_API_KEY",
                "protocol": protocol,
            }
        },
        "models": {
            "flash": {
                "model": "qwen3.8-flash",
                "endpoint": "ms",
                "harness": harness,
                "limits": {"context": 991808, "output": 131072},
            }
        },
    }
    return profiles.validate_profiles(doc, registry=REGISTRY)["models"]


class OpenCodeSyncTests(unittest.TestCase):
    def plan(self, protocol: str) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as tmp:
            _path, _before, after = get_adapter("opencode").plan_endpoint_sync(
                entries(protocol), {"XDG_CONFIG_HOME": tmp}, Path(tmp)
            )
        return json.loads(after)["provider"]["flash"]

    def test_openai_protocol_block(self) -> None:
        block = self.plan("openai")
        self.assertEqual("@ai-sdk/openai-compatible", block["npm"])
        self.assertEqual(
            "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
            block["options"]["baseURL"],
        )
        self.assertIs(True, block["options"]["includeUsage"])
        self.assertEqual("{env:MODEL_STUDIO_API_KEY}", block["options"]["apiKey"])
        model = block["models"]["qwen3.8-flash"]
        self.assertEqual({"context": 991808, "output": 131072}, model["limit"])
        self.assertEqual({"reasoningEffort": "medium"}, model["variants"]["medium"])

    def test_anthropic_protocol_block(self) -> None:
        block = self.plan("anthropic")
        self.assertEqual("@ai-sdk/anthropic", block["npm"])
        self.assertEqual(
            "https://token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic/v1",
            block["options"]["baseURL"],
        )
        self.assertNotIn("includeUsage", block["options"])
        self.assertNotIn("variants", block["models"]["qwen3.8-flash"])


class PiSyncTests(unittest.TestCase):
    def test_reasoning_flag_for_models_with_effort(self) -> None:
        model = get_adapter("pi")._managed_model(entries(harness="pi")["flash"])
        self.assertIs(True, model["reasoning"])


if __name__ == "__main__":
    unittest.main()
