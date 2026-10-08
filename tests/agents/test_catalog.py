"""Tests for the lightweight open-weight model catalog."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from pitwall.agents.catalog import (
    CatalogError,
    known_family_ids,
    load_catalog,
    validate_catalog,
)
from pitwall.agents.registry import (
    load_registry,
)

ROOT = Path(__file__).resolve().parents[2]


class CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_registry()
        cls.catalog = load_catalog(registry=cls.registry)

    def test_seed_catalog_is_valid_and_first_party_linked(self) -> None:
        models = self.catalog["models"]
        self.assertIn("kimi-k3", models)
        self.assertIn("glm-5.3-flash", models)
        self.assertTrue(
            all(entry["modelCardUrl"].startswith("https://") for entry in models.values())
        )
        self.assertEqual("glm", models["glm-5.3-flash"]["family"])

    def test_family_references_must_exist(self) -> None:
        self.assertIn("glm", known_family_ids(self.registry))
        data = json.loads(json.dumps(self.catalog))
        data["models"]["kimi-k3"]["family"] = "no-such-family"
        with self.assertRaisesRegex(CatalogError, "no-such-family"):
            validate_catalog(data, registry=self.registry)

    def test_ids_fields_and_urls_are_checked(self) -> None:
        base = json.loads(json.dumps(self.catalog))
        bad_id = json.loads(json.dumps(base))
        bad_id["models"]["Bad_ID"] = bad_id["models"]["kimi-k3"]
        with self.assertRaisesRegex(CatalogError, "catalog id"):
            validate_catalog(bad_id, registry=self.registry)
        missing = json.loads(json.dumps(base))
        del missing["models"]["kimi-k3"]["license"]
        with self.assertRaisesRegex(CatalogError, "license"):
            validate_catalog(missing, registry=self.registry)
        insecure = json.loads(json.dumps(base))
        insecure["models"]["kimi-k3"]["modelCardUrl"] = "http://example.invalid"
        with self.assertRaisesRegex(CatalogError, "https"):
            validate_catalog(insecure, registry=self.registry)


if __name__ == "__main__":
    unittest.main()
