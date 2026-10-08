"""[agents.profiles] accepts Model Studio endpoints and enforces the catalog on their routes."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from pitwall.agents import profiles
from pitwall.agents.registry import (
    load_registry,
)

ROOT = Path(__file__).resolve().parents[2]

REGISTRY = load_registry()
BASE = "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"


def document(**route: object) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "endpoints": {
            "ms": {
                "kind": "model-studio",
                "plan": "token-plan-personal",
                "tier": "pro",
                "apiKeyEnv": "MODEL_STUDIO_API_KEY",
            }
        },
        "models": {
            "flash": {"model": "qwen3.8-flash", "endpoint": "ms", "harness": "opencode", **route}
        },
    }


class ModelStudioRoutesTests(unittest.TestCase):
    def test_endpoint_normalizes_with_derived_base_url(self) -> None:
        data = profiles.validate_profiles(document(), registry=REGISTRY)
        endpoint = profiles.resolved_endpoint(data["models"]["flash"])
        assert endpoint is not None
        self.assertEqual(BASE, endpoint["baseUrl"])
        self.assertEqual("model-studio", endpoint["kind"])

    def test_saved_endpoint_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {"PITWALL_AGENTS_PROFILES": os.path.join(tmp, "profiles.toml")}
            profiles.save_profiles(env, document(), registry=REGISTRY)
            first = Path(env["PITWALL_AGENTS_PROFILES"]).read_text(encoding="utf-8")
            profiles.save_profiles(
                env, profiles.load_profiles(env, registry=REGISTRY), registry=REGISTRY
            )
            self.assertEqual(
                first, Path(env["PITWALL_AGENTS_PROFILES"]).read_text(encoding="utf-8")
            )
            self.assertIn(BASE, first)

    def test_ineligible_model_is_refused(self) -> None:
        doc = document()
        doc["models"]["flash"]["model"] = "kimi-k2.7-code"  # type: ignore[index]  # reason: the test navigates a loosely typed JSON document
        with self.assertRaisesRegex(profiles.ProfilesError, "model_not_eligible"):
            profiles.validate_profiles(doc, registry=REGISTRY)

    def test_effort_uses_the_model_vocabulary(self) -> None:
        profiles.validate_profiles(document(effort="medium"), registry=REGISTRY)
        with self.assertRaisesRegex(profiles.ProfilesError, "xhigh, medium, low"):
            profiles.validate_profiles(document(effort="high"), registry=REGISTRY)

    def test_anthropic_protocol_is_opencode_only_and_takes_no_effort(self) -> None:
        doc = document()
        doc["endpoints"]["ms"]["protocol"] = "anthropic"  # type: ignore[index]  # reason: the test navigates a loosely typed JSON document
        profiles.validate_profiles(doc, registry=REGISTRY)
        doc["models"]["flash"]["harness"] = "pi"  # type: ignore[index]  # reason: the test navigates a loosely typed JSON document
        with self.assertRaisesRegex(profiles.ProfilesError, "anthropic"):
            profiles.validate_profiles(doc, registry=REGISTRY)
        doc["models"]["flash"]["harness"] = "opencode"  # type: ignore[index]  # reason: the test navigates a loosely typed JSON document
        doc["models"]["flash"]["effort"] = "low"  # type: ignore[index]  # reason: the test navigates a loosely typed JSON document
        with self.assertRaisesRegex(profiles.ProfilesError, "effort"):
            profiles.validate_profiles(doc, registry=REGISTRY)

    def test_plain_endpoints_are_unchanged(self) -> None:
        doc = {
            "schemaVersion": 1,
            "endpoints": {"local": {"baseUrl": "http://127.0.0.1:8000/v1"}},
            "models": {},
        }
        self.assertEqual(
            {"baseUrl": "http://127.0.0.1:8000/v1"},
            profiles.validate_profiles(doc, registry=REGISTRY)["endpoints"]["local"],
        )


if __name__ == "__main__":
    unittest.main()
