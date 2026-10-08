"""The model facts validator: one passing and one failing case per rule."""

from __future__ import annotations

import copy
import datetime
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

from tools.agents import (
    validate_model_facts as vmf,
)

ROOT = Path(__file__).resolve().parents[2]

TODAY = datetime.date(2026, 9, 28)
EVIDENCE = [{"source": "vendor-model", "locator": "At a glance"}]


def fact(value: Any, **extra: Any) -> dict[str, Any]:
    return {"value": value, "method": "reviewed", "evidence": copy.deepcopy(EVIDENCE), **extra}


def base_sources() -> dict[str, Any]:
    def source(
        source_id: str, page_type: str, kind: str = "vendor", **extra: Any
    ) -> dict[str, Any]:
        return {
            "id": source_id,
            "url": f"https://docs.example/{source_id}.md",
            "kind": kind,
            "pageType": page_type,
            "fetch": "http",
            "format": "markdown",
            **extra,
        }

    return {
        "schemaVersion": 1,
        "unit": "families/demo",
        "notPublished": [
            {
                "pageType": "guidance",
                "kind": "vendor",
                "searchedAt": "2026-09-28",
                "searched": "every heading in the index",
            },
        ],
        "watch": [],
        "sources": [
            source("vendor-model", "model"),
            source("vendor-effort", "effort"),
            source("vendor-changes", "changes"),
            {
                "id": "hf-demo",
                "url": "https://huggingface.co/vendor/Demo-1",
                "kind": "artifact",
                "pageType": "model",
                "fetch": "huggingface",
                "format": "json",
                "repo": "vendor/Demo-1",
            },
        ],
    }


def base_facts() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "unit": "families/demo",
        "models": {
            "demo-1": {
                "status": "current",
                "displayName": "Demo 1",
                "artifact": "vendor/Demo-1",
                "routes": [{"harness": "grok", "model": "demo-1", "register": True}],
                "facts": {"contextWindow": fact(500000), "effortValues": fact(["low", "high"])},
            }
        },
        "guidance": [
            {
                "id": "g1",
                "text": "Pass the whole reply back on every turn that used a tool.",
                "applies": ["demo-1"],
                "class": "vendor",
                "source": "vendor-model",
                "locator": "Notes",
                "surfaces": ["card"],
                "reviewedAt": "2026-09-28",
            }
        ],
    }


def base_registry() -> dict[str, Any]:
    return {
        "harnesses": {
            "grok": {
                "effort": {"kind": "harness-flag", "values": ["low", "medium", "high"]},
                "defaultModel": {"source": "registry", "fallback": "demo-1"},
                "models": {},
            }
        }
    }


class Tree:
    def __init__(
        self,
        sources: dict[str, Any],
        facts: dict[str, Any] | None,
        registry: dict[str, Any],
        extra: dict[str, tuple[dict[str, Any], dict[str, Any] | None]] | None = None,
    ) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        shutil.copytree(
            ROOT / "docs/agents/model-facts" / "schemas",
            self.root / "docs/agents/model-facts" / "schemas",
        )
        registry_path = self.root / vmf.REGISTRY
        registry_path.parent.mkdir(parents=True)
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        for unit, (unit_sources, unit_facts) in {
            "families/demo": (sources, facts),
            **(extra or {}),
        }.items():
            base = self.root / "docs/agents/model-facts" / unit
            base.mkdir(parents=True)
            (base / "sources.json").write_text(json.dumps(unit_sources), encoding="utf-8")
            if unit_facts is not None:
                (base / "facts.json").write_text(json.dumps(unit_facts), encoding="utf-8")

    def cache(self, unit: str, source_id: str, text: str) -> None:
        path = self.root / "docs/agents/model-facts" / ".cache" / unit / f"{source_id}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


class ValidatorTests(unittest.TestCase):
    def problems(
        self,
        sources: dict[str, Any] | None = None,
        facts: dict[str, Any] | None = None,
        registry: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> list[vmf.Problem]:
        tree = Tree(
            sources or base_sources(),
            facts if facts is not None else base_facts(),
            registry or base_registry(),
            kwargs.get("extra"),
        )
        self.addCleanup(tree.tmp.cleanup)
        for unit, source_id, text in kwargs.get("cache", []):
            tree.cache(unit, source_id, text)
        return vmf.validate(tree.root, TODAY)

    def errors(self, *args: Any, **kwargs: Any) -> list[str]:
        return [
            problem.message
            for problem in self.problems(*args, **kwargs)
            if problem.level == "error"
        ]

    def assert_error(self, fragment: str, *args: Any, **kwargs: Any) -> None:
        errors = self.errors(*args, **kwargs)
        self.assertTrue(any(fragment in error for error in errors), errors)

    def test_the_base_tree_is_valid(self) -> None:
        self.assertEqual([], self.problems())

    def test_schema(self) -> None:
        facts = base_facts()
        facts["models"]["demo-1"]["colour"] = "blue"
        self.assert_error("Additional properties are not allowed", facts=facts)

    def test_evidence_source_must_be_listed(self) -> None:
        facts = base_facts()
        facts["models"]["demo-1"]["facts"]["contextWindow"]["evidence"][0]["source"] = "nowhere"
        self.assert_error("evidence source nowhere is not in sources.json", facts=facts)

    def test_guidance_source_must_be_listed(self) -> None:
        facts = base_facts()
        facts["guidance"][0]["source"] = "nowhere"
        self.assert_error("source nowhere is not in sources.json", facts=facts)

    def test_every_required_page_type_is_covered(self) -> None:
        sources = base_sources()
        sources["notPublished"] = []
        self.assert_error(
            "page type guidance has no source and no notPublished entry", sources=sources
        )

    def test_guidance_class_equals_source_kind(self) -> None:
        facts = base_facts()
        facts["guidance"][0]["class"] = "host"
        self.assert_error("class host is not the source kind vendor", facts=facts)

    def test_a_disagreement_needs_a_note(self) -> None:
        facts = base_facts()
        facts["models"]["demo-1"]["facts"]["effortValues"]["evidence"].append(
            {
                "source": "vendor-effort",
                "locator": "Table",
                "states": ["low", "medium"],
                "disagrees": True,
            }
        )
        self.assert_error("has a disagreeing source but no note", facts=facts)
        facts["models"]["demo-1"]["facts"]["effortValues"]["note"] = "The template rejects medium."
        self.assertEqual([], self.errors(facts=facts))

    def test_text_and_quote_lengths(self) -> None:
        facts = base_facts()
        facts["guidance"][0]["text"] = "x" * 241
        self.assert_error("text is longer than 240 characters", facts=facts)
        facts = base_facts()
        facts["guidance"][0]["quote"] = " ".join(["word"] * 26)
        self.assert_error("quote is longer than 25 words", facts=facts)

    def test_a_retiring_model_needs_a_date(self) -> None:
        facts = base_facts()
        facts["models"]["demo-1"]["status"] = "retiring"
        self.assert_error("status retiring needs a retires fact", facts=facts)

    def test_no_default_names_a_model_retiring_within_thirty_days(self) -> None:
        facts = base_facts()
        facts["models"]["demo-1"]["status"] = "retiring"
        facts["models"]["demo-1"]["facts"]["retires"] = fact("2026-10-21")
        self.assert_error("demo-1 retires 2026-10-21; choose another default", facts=facts)
        facts["models"]["demo-1"]["facts"]["retires"] = fact("2027-01-01")
        self.assertEqual([], self.errors(facts=facts))

    def test_no_default_names_a_retired_model(self) -> None:
        facts = base_facts()
        facts["models"]["demo-1"]["status"] = "retired"
        facts["models"]["demo-1"]["facts"]["retires"] = fact("2027-01-01")
        self.assert_error("choose another default", facts=facts)

    def test_harness_effort_values_cover_registered_models(self) -> None:
        facts = base_facts()
        facts["models"]["demo-1"]["facts"]["effortValues"] = fact(["low", "xhigh"])
        self.assert_error("harness grok effort.values lacks xhigh", facts=facts)
        facts["models"]["demo-1"]["routes"][0]["effortValues"] = ["low"]
        self.assertEqual([], self.errors(facts=facts))

    def test_a_harness_without_effort_control_takes_an_empty_route_list(self) -> None:
        registry = base_registry()
        registry["harnesses"]["grok"]["effort"] = {"kind": "none", "values": []}
        self.assert_error("has no effort control", registry=registry)
        facts = base_facts()
        facts["models"]["demo-1"]["routes"][0]["effortValues"] = []
        self.assertEqual([], self.errors(facts=facts, registry=registry))

    def test_a_model_without_effort_control_records_an_empty_effort_list(self) -> None:
        facts = base_facts()
        facts["models"]["demo-1"]["facts"]["effortValues"] = fact([])
        facts["models"]["demo-1"]["routes"][0]["effortValues"] = []
        self.assertEqual([], self.errors(facts=facts))

    def test_other_list_facts_stay_non_empty(self) -> None:
        self.assertTrue(vmf._type_ok("list", [], "effortValues"))
        self.assertFalse(vmf._type_ok("list", []))
        self.assertFalse(vmf._type_ok("list", [], "somethingElse"))

    def test_unknown_harness(self) -> None:
        facts = base_facts()
        facts["models"]["demo-1"]["routes"][0]["harness"] = "nope"
        self.assert_error("harness nope is not a registry harness", facts=facts)

    def test_closed_fact_keys_and_types(self) -> None:
        facts = base_facts()
        facts["models"]["demo-1"]["facts"]["price"] = fact(3)
        self.assert_error("price is not a fact key for this unit", facts=facts)
        facts = base_facts()
        facts["models"]["demo-1"]["facts"]["contextWindow"] = fact("large")
        self.assert_error("contextWindow value has the wrong type", facts=facts)

    def test_extraction_is_limited(self) -> None:
        facts = base_facts()
        facts["models"]["demo-1"]["facts"]["effortValues"]["method"] = "extracted"
        self.assert_error("effortValues cannot be extracted", facts=facts)
        facts = base_facts()
        facts["models"]["demo-1"]["facts"]["contextWindow"]["method"] = "extracted"
        self.assert_error("is not a Hugging Face source", facts=facts)

    def test_applies_names_known_models(self) -> None:
        facts = base_facts()
        facts["guidance"][0]["applies"] = ["demo-9"]
        self.assert_error("applies to unknown models: demo-9", facts=facts)

    def test_two_families_cannot_claim_one_model(self) -> None:
        other_sources = dict(base_sources(), unit="families/other")
        other_facts = dict(base_facts(), unit="families/other")
        other_facts["models"]["demo-1"]["routes"] = []
        self.assert_error(
            "demo-1 is also claimed by families/demo",
            extra={"families/other": (other_sources, other_facts)},
        )

    def test_guidance_that_copies_its_source(self) -> None:
        text = base_facts()["guidance"][0]["text"]
        source = f"Intro. {text} More."
        self.assert_error(
            "text copies its source", cache=[("families/demo", "vendor-model", source)]
        )
        self.assertEqual(
            [], self.errors(cache=[("families/demo", "vendor-model", "Unrelated page text.")])
        )

    def test_pending_and_stale_are_warnings(self) -> None:
        sources = base_sources()
        sources["sources"][0].update(
            reviewedHash="sha256:" + "1" * 64, observedHash="sha256:" + "2" * 64
        )
        sources["sources"][1].update(fetch="manual", reviewedAt="2026-07-01")
        problems = self.problems(sources=sources)
        self.assertEqual(["warning", "warning"], [problem.level for problem in problems])
        self.assertEqual(0, len([p for p in problems if p.level == "error"]))

    def test_a_host_model_names_its_family_model(self) -> None:
        host_sources = {
            "schemaVersion": 1,
            "unit": "hosts/go",
            "notPublished": [],
            "watch": [],
            "sources": [
                {
                    "id": "go-docs",
                    "url": "https://host.example/go/",
                    "kind": "host",
                    "pageType": "model",
                    "fetch": "http",
                    "format": "html",
                },
                {
                    "id": "go-changes",
                    "url": "https://host.example/changes/",
                    "kind": "host",
                    "pageType": "changes",
                    "fetch": "http",
                    "format": "html",
                },
            ],
        }
        host_facts = {
            "schemaVersion": 1,
            "unit": "hosts/go",
            "guidance": [],
            "models": {
                "go/demo-1": {
                    "of": "families/demo#demo-9",
                    "facts": {
                        "webSearch": {
                            "value": False,
                            "method": "reviewed",
                            "evidence": [{"source": "go-docs", "locator": "Models"}],
                        }
                    },
                }
            },
        }
        self.assert_error(
            "does not name a family model", extra={"hosts/go": (host_sources, host_facts)}
        )
        host_facts["models"]["go/demo-1"]["of"] = "families/demo#demo-1"
        self.assertEqual([], self.errors(extra={"hosts/go": (host_sources, host_facts)}))
        tree = Tree(
            base_sources(), base_facts(), base_registry(), {"hosts/go": (host_sources, host_facts)}
        )
        self.addCleanup(tree.tmp.cleanup)
        only_host = vmf.validate(tree.root, TODAY, units=["hosts/go"])
        self.assertEqual([], [p for p in only_host if p.level == "error"], "a host validates alone")


class CopiedRunTests(unittest.TestCase):
    def test_eleven_words_is_not_a_copy(self) -> None:
        text = "one two three four five six seven eight nine ten eleven"
        self.assertIsNone(vmf.copied_run(text, text))

    def test_twelve_words_ignoring_case_and_punctuation(self) -> None:
        source = "One, two; three four five six seven eight nine ten eleven twelve!"
        self.assertIsNotNone(
            vmf.copied_run("one two three four five six seven eight nine ten eleven twelve", source)
        )


if __name__ == "__main__":
    unittest.main()
