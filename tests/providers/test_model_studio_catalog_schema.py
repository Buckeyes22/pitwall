"""The committed Model Studio catalog is schema-valid and internally consistent."""

from __future__ import annotations

import unittest
from pathlib import Path

from pitwall.agents.resources import read_resource_json
from pitwall.providers.model_studio.catalog import load_catalog

ROOT = Path(__file__).resolve().parents[2]


class ModelStudioCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = load_catalog()

    def test_catalog_validates_against_its_schema(self) -> None:
        import jsonschema  # dev dependency, as in tools/agents/validate_json_schemas.py

        schema = read_resource_json("schemas/model-studio.schema.json")
        jsonschema.Draft202012Validator(schema).validate(self.catalog)

    def test_every_plan_region_is_a_known_region(self) -> None:
        for name, plan in self.catalog["plans"].items():
            for region in plan["regions"]:
                self.assertIn(region, self.catalog["regions"], name)

    def test_token_plans_are_singapore_only_with_sk_sp_keys(self) -> None:
        for plan in self.catalog["plans"].values():
            if plan["family"] == "token-plan":
                self.assertEqual(["ap-southeast-1"], plan["regions"])
                self.assertEqual("sk-sp-", plan["keyPrefix"])
                self.assertTrue(plan["tiers"])

    def test_text_models_publish_limits_except_the_router(self) -> None:
        for name, model in self.catalog["models"].items():
            if model["kind"] != "text" or model["reasoning"] == "router":
                continue
            self.assertIsNotNone(model["maxInputTokens"], name)
            self.assertIsNotNone(model["maxOutputTokens"], name)
            self.assertLessEqual(model["maxOutputTokens"], model["contextTokens"], name)

    def test_effort_defaults_are_members_of_their_values(self) -> None:
        for name, model in self.catalog["models"].items():
            effort = model.get("effort")
            if effort:
                self.assertIn(effort["default"], effort["values"], name)

    def test_price_tiers_ascend(self) -> None:
        for name, model in self.catalog["models"].items():
            prices = model.get("prices") or {}
            thresholds = [tier["aboveInputTokens"] for tier in prices.get("tiers", [])]
            self.assertEqual(sorted(thresholds), thresholds, name)

    def test_personal_pro_is_180000_credits_with_up_to_eight_agents(self) -> None:
        pro = self.catalog["plans"]["token-plan-personal"]["tiers"]["pro"]
        self.assertEqual({"credits": 180000, "concurrency": [6, 8]}, pro)


if __name__ == "__main__":
    unittest.main()
