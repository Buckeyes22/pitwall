"""Tests for canonical registry validation and host-specific generation."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from pitwall.agents.registry import (
    RegistryError,
    load_registry,
    validate_registry,
    validate_source_layout,
)
from tools.agents.sync_routes import (
    _display_routes,
    generated_files,
)

ROOT = Path(__file__).resolve().parents[2]
RESOURCE_ROOT = ROOT / "src/pitwall/agents/resources"


class RegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_registry()

    def test_schema_documents_are_valid_json(self) -> None:
        for path in (RESOURCE_ROOT / "schemas").glob("*.schema.json"):
            with self.subTest(path=path.name):
                self.assertIsInstance(json.loads(path.read_text(encoding="utf-8")), dict)

    def test_generated_references_are_one_host_neutral_copy(self) -> None:
        files = generated_files(self.registry)
        self.assertEqual(
            {"routes.generated.md", "harness-registry.generated.json"},
            {path.name for path in files},
        )
        self.assertEqual({RESOURCE_ROOT / "generated"}, {path.parent for path in files})
        payload = json.loads(
            next(content for path, content in files.items() if path.suffix == ".json")
        )
        self.assertEqual(
            {"claude": ["claude"], "codex": ["codex"], "copilot": []}, payload["hosts"]
        )
        self.assertEqual(
            {
                "agy",
                "cline",
                "codex",
                "claude",
                "dsh",
                "goose",
                "grok",
                "hermes",
                "kimi",
                "muse",
                "opencode",
                "pi",
                "qwen",
                "zcode",
            },
            set(payload["harnesses"]),
        )

    def test_generated_files_match_committed_content(self) -> None:
        for path, expected in generated_files(self.registry).items():
            with self.subTest(path=str(path)):
                self.assertTrue(path.is_file(), path)
                self.assertEqual(expected, path.read_text(encoding="utf-8"))

    def test_registry_has_no_mythos_surface(self) -> None:
        self.assertNotIn("mythos", json.dumps(self.registry).lower())

    def test_duplicate_model_alias_is_rejected(self) -> None:
        data = json.loads(json.dumps(self.registry))
        data["harnesses"]["grok"]["models"]["grok-4.7"]["aliases"] = ["sonnet"]
        with self.assertRaisesRegex(RegistryError, "duplicated"):
            validate_registry(data)

    def test_registry_adapter_contracts_are_aligned(self) -> None:
        validate_registry(json.loads(json.dumps(self.registry)))
        validate_source_layout(self.registry, repo_root=ROOT)

    def test_qwen_declares_the_standalone_installer_shim_as_a_binary_candidate(self) -> None:
        qwen = self.registry["harnesses"]["qwen"]
        self.assertEqual(["qwen", "$HOME/.local/bin/qwen"], qwen["binaryCandidates"])
        self.assertEqual("QWEN_BIN", qwen["binaryOverrideEnv"])

    def test_qwen_declares_qwen_config_source_and_no_effort_control(self) -> None:
        qwen = self.registry["harnesses"]["qwen"]
        self.assertEqual("qwen-config", qwen["defaultModel"]["source"])
        self.assertEqual("qwen-default", qwen["defaultModel"]["fallback"])
        self.assertEqual({"kind": "none", "key": None, "values": []}, qwen["effort"])
        self.assertTrue(qwen["allowUnknownModels"])

    def test_agy_declares_antigravity_contract_and_gemini_models(self) -> None:
        from pitwall.agents.profiles import find_vendor_harness

        agy = self.registry["harnesses"]["agy"]
        self.assertEqual(["agy", "$HOME/.local/bin/agy"], agy["binaryCandidates"])
        self.assertEqual("AGY_BIN", agy["binaryOverrideEnv"])
        self.assertEqual("gemini-3.8-flash", agy["defaultModel"]["fallback"])
        self.assertEqual(
            {"kind": "harness-flag", "key": "--effort", "values": ["low", "medium", "high"]},
            agy["effort"],
        )
        self.assertEqual(["low", "high"], agy["models"]["gemini-3.1-pro"]["effortValues"])
        self.assertEqual("agy", find_vendor_harness(self.registry, "gemini-3.7-flash-high"))

    def test_kimi_declares_config_probe_and_no_effort_control(self) -> None:
        kimi = self.registry["harnesses"]["kimi"]
        self.assertTrue(kimi["capabilities"]["configProbe"])
        self.assertEqual({"kind": "none", "key": None, "values": []}, kimi["effort"])

    def test_required_harness_objects_are_rejected_when_missing(self) -> None:
        for field in ("effort", "capabilities"):
            with self.subTest(field=field):
                data = json.loads(json.dumps(self.registry))
                del data["harnesses"]["codex"][field]
                with self.assertRaisesRegex(RegistryError, field):
                    validate_registry(data)

    def test_required_model_and_family_fields_are_rejected_when_missing(self) -> None:
        cases = (
            ("models", "displayName"),
            ("models", "provenance"),
            ("routeFamilies", "displayName"),
        )
        for collection, field in cases:
            with self.subTest(collection=collection, field=field):
                data = json.loads(json.dumps(self.registry))
                if collection == "models":
                    del data["harnesses"]["codex"][collection]["gpt-5.6-sol"][field]
                else:
                    del data["harnesses"]["opencode"][collection][0][field]
                with self.assertRaisesRegex(RegistryError, field):
                    validate_registry(data)

    def test_invalid_ids_and_empty_required_lists_are_rejected(self) -> None:
        for family_id in ("Kimi", "../etc"):
            with self.subTest(family_id=family_id):
                data = json.loads(json.dumps(self.registry))
                data["harnesses"]["opencode"]["routeFamilies"][0]["id"] = family_id
                with self.assertRaisesRegex(RegistryError, "invalid format|parent path"):
                    validate_registry(data)
        data = json.loads(json.dumps(self.registry))
        data["harnesses"]["codex"]["models"]["../foo"] = data["harnesses"]["codex"]["models"][
            "gpt-5.6-sol"
        ]
        with self.assertRaisesRegex(RegistryError, "invalid format|parent path"):
            validate_registry(data)
        for field, value in (("binaryCandidates", []), ("routeFamilies", [])):
            data = json.loads(json.dumps(self.registry))
            if field == "binaryCandidates":
                data["harnesses"]["codex"][field] = value
            else:
                data["harnesses"]["opencode"][field][0]["patterns"] = value
            with self.assertRaisesRegex(RegistryError, "at least 1"):
                validate_registry(data)

    def test_positional_default_must_not_have_fallback(self) -> None:
        data = json.loads(json.dumps(self.registry))
        data["harnesses"]["opencode"]["defaultModel"]["fallback"] = "unexpected"
        with self.assertRaisesRegex(RegistryError, "null fallback"):
            validate_registry(data)

    def test_native_ownership_must_be_reciprocal(self) -> None:
        data = json.loads(json.dumps(self.registry))
        data["hosts"]["claude"]["nativeHarnesses"] = ["grok"]
        with self.assertRaisesRegex(RegistryError, "disagree"):
            validate_registry(data)

    def test_runtime_reference_anchor_must_exist(self) -> None:
        data = json.loads(json.dumps(self.registry))
        data["harnesses"]["codex"]["models"]["gpt-5.6-sol"]["runtimeReference"] = (
            "references/model-prompting.md#missing-anchor"
        )
        with self.assertRaisesRegex(RegistryError, "anchor does not exist"):
            validate_source_layout(data, repo_root=ROOT)

    def test_registry_paths_must_remain_inside_repository(self) -> None:
        data = json.loads(json.dumps(self.registry))
        data["hosts"]["copilot"]["packagePath"] = "../outside"
        with self.assertRaisesRegex(RegistryError, "unsafe path"):
            validate_registry(data)

        data = json.loads(json.dumps(self.registry))
        data["harnesses"]["codex"]["models"]["gpt-5.6-sol"]["promptReference"] = (
            "/outside/prompt.md"
        )
        with self.assertRaisesRegex(RegistryError, "relative to the repository"):
            validate_registry(data)

    def test_harness_kind_and_endpoint_delivery_are_declared(self) -> None:
        harnesses = self.registry["harnesses"]
        expected = {
            "codex": ("model-bound", "none"),
            "claude": ("model-bound", "none"),
            "grok": ("model-bound", "none"),
            "kimi": ("model-bound", "none"),
            "opencode": ("model-agnostic", "config-sync"),
            "qwen": ("model-agnostic", "env"),
            "hermes": ("model-agnostic", "config-sync"),
            "goose": ("model-agnostic", "env"),
            "agy": ("model-bound", "none"),
        }
        for harness_id, (kind, delivery) in expected.items():
            with self.subTest(harness=harness_id):
                self.assertEqual(kind, harnesses[harness_id]["harnessKind"])
                self.assertEqual(delivery, harnesses[harness_id]["endpointDelivery"])
        self.assertEqual(
            {"baseUrl": "OPENAI_BASE_URL", "apiKey": "OPENAI_API_KEY", "model": "QWEN_MODEL"},
            harnesses["qwen"]["endpointEnv"],
        )
        self.assertNotIn("endpointEnv", harnesses["hermes"])
        self.assertEqual(
            "${HERMES_HOME:-~/.hermes}/config.yaml",
            harnesses["hermes"]["configSync"]["path"],
        )
        self.assertEqual(
            {"baseUrl": "OPENAI_HOST", "apiKey": "OPENAI_API_KEY", "model": "GOOSE_MODEL"},
            harnesses["goose"]["endpointEnv"],
        )
        self.assertEqual(
            "${XDG_CONFIG_HOME:-~/.config}/opencode/opencode.json",
            harnesses["opencode"]["configSync"]["path"],
        )

    def test_endpoint_delivery_requires_its_companion_object(self) -> None:
        for harness_id, drop in (("qwen", "endpointEnv"), ("opencode", "configSync")):
            with self.subTest(harness=harness_id):
                data = json.loads(json.dumps(self.registry))
                del data["harnesses"][harness_id][drop]
                with self.assertRaisesRegex(RegistryError, drop):
                    validate_registry(data)
        data = json.loads(json.dumps(self.registry))
        data["harnesses"]["codex"]["endpointEnv"] = {"baseUrl": "X", "apiKey": "Y", "model": "Z"}
        with self.assertRaisesRegex(RegistryError, "endpointEnv"):
            validate_registry(data)

    def test_registry_and_adapter_agree_about_endpoint_delivery(self) -> None:
        data = json.loads(json.dumps(self.registry))
        data["harnesses"]["qwen"]["endpointDelivery"] = "none"
        del data["harnesses"]["qwen"]["endpointEnv"]
        with self.assertRaisesRegex(RegistryError, "endpoint delivery"):
            validate_registry(data)

    def test_file_delivery_and_new_default_sources_are_accepted(self) -> None:
        data = json.loads(json.dumps(self.registry))
        data["harnesses"]["qwen"]["promptDelivery"] = "file"
        with self.assertRaisesRegex(RegistryError, "prompt delivery"):
            validate_registry(data)  # adapter parity still enforced
        for source in ("pi-config", "hermes-config", "cline-config", "goose-config", "dsh-config"):
            with self.subTest(source=source):
                data = json.loads(json.dumps(self.registry))
                data["harnesses"]["qwen"]["defaultModel"] = {
                    "source": source,
                    "fallback": "x-default",
                }
                validate_registry(data)
        data = json.loads(json.dumps(self.registry))
        data["harnesses"]["qwen"]["defaultModel"] = {"source": "nope-config", "fallback": "x"}
        with self.assertRaisesRegex(RegistryError, "default model source"):
            validate_registry(data)

    def test_seed_model_families_are_present(self) -> None:
        families = self.registry["modelFamilies"]
        self.assertIn("muse-glimmer", families)
        self.assertEqual("muse-glimmer.md", Path(families["muse-glimmer"]["capabilityCard"]).name)
        self.assertIn("deepseek-v4", families)
        self.assertEqual("deepseek.md", Path(families["deepseek-v4"]["capabilityCard"]).name)
        self.assertIn("gemma-4", families)
        self.assertEqual("gemma.md", Path(families["gemma-4"]["capabilityCard"]).name)

    def synthetic_family(self) -> dict[str, object]:
        # Points at files that already exist so path/anchor checks pass without new docs.
        return {
            "displayName": "Synthetic Family",
            "vendor": "Test",
            "patterns": ["synthetic-*", "test-org/Synthetic*"],
            "models": ["test-org/Synthetic-7B"],
            "license": "Apache-2.0",
            "contextWindow": 131072,
            "reasoningControl": {
                "kind": "request-parameter",
                "detail": "reasoning_effort low|high",
            },
            "modelCardUrl": "https://huggingface.co/test-org/Synthetic-7B",
            "promptReference": "docs/prompting/qwen-alibaba-prompting-reference.md",
            "runtimeReference": "references/model-prompting.md#qwen",
            "capabilityCard": "plugins/claude/skills/subagent-model-routing/ledger/qwen.md",
            "provenance": "test",
        }

    def test_model_families_validate_and_resolve_by_pattern(self) -> None:
        from pitwall.agents.registry import family_for_model

        data = json.loads(json.dumps(self.registry))
        data["modelFamilies"] = {"synthetic": self.synthetic_family()}
        validate_registry(data)
        self.assertEqual("synthetic", family_for_model(data, "test-org/synthetic-7b"))
        self.assertIsNone(family_for_model(data, "gpt-5.6-sol"))

    def test_model_family_patterns_and_models_must_be_unique(self) -> None:
        data = json.loads(json.dumps(self.registry))
        clash = self.synthetic_family()
        clash["patterns"] = ["glm-*"]  # already owned by opencode's glm route family
        data["modelFamilies"] = {"synthetic": clash}
        with self.assertRaisesRegex(RegistryError, "pattern"):
            validate_registry(data)
        data = json.loads(json.dumps(self.registry))
        owned = self.synthetic_family()
        owned["models"] = ["gpt-5.6-sol"]
        data["modelFamilies"] = {"synthetic": owned}
        with self.assertRaisesRegex(RegistryError, "gpt-5.6-sol"):
            validate_registry(data)

    def test_model_families_render_into_the_generated_references(self) -> None:
        data = json.loads(json.dumps(self.registry))
        data["modelFamilies"] = {"synthetic": self.synthetic_family()}
        files = generated_files(
            data, catalog={"schemaVersion": 1, "sourceVerifiedOn": "2026-08-26", "models": {}}
        )
        for path, content in files.items():
            with self.subTest(path=path.name):
                if path.suffix == ".md":
                    self.assertIn("## Open-weight models", content)
                    self.assertIn("| `synthetic` | Synthetic Family |", content)
                else:
                    self.assertIn("synthetic", json.loads(content)["modelFamilies"])

    def test_route_table_lists_each_model_once(self) -> None:
        for harness_id, harness in self.registry["harnesses"].items():
            with self.subTest(harness=harness_id):
                labels = _display_routes(harness).split(", ")
                self.assertEqual(len(labels), len(set(labels)))


if __name__ == "__main__":
    unittest.main()
