"""The model facts generator: markers, blocks, registry fields, facts sheets, and placement."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

from tools.agents import (
    sync_model_facts as smf,
)
from tools.agents.model_facts_common import (
    HOSTS,
    LEDGER,
    REFERENCE,
    REGISTRY,
    SKILL,
)

ROOT = Path(__file__).resolve().parents[2]

KNOWN = {"grok", "kimi"}
EVIDENCE = [{"source": "vendor-model", "locator": "At a glance"}]

CARD_MARKERS = {
    "claude": [
        "codex",
        "gemini",
        "glm",
        "grok",
        "hy",
        "kimi",
        "longcat",
        "mimo",
        "minimax",
        "qwen",
    ],
    "codex": [
        "claude-fable-5",
        "claude-haiku-5",
        "claude-opus-4.8",
        "claude-sonnet-5",
        "gemini",
        "glm",
        "grok",
        "hy",
        "kimi",
        "longcat",
        "mimo",
        "minimax",
        "muse-spark",
        "qwen",
    ],
    "copilot": [
        "claude-fable-5",
        "claude-haiku-5",
        "claude-opus-4.8",
        "claude-sonnet-5",
        "codex",
        "gemini",
        "glm",
        "grok",
        "hy",
        "kimi",
        "longcat",
        "mimo",
        "minimax",
        "muse-spark",
        "qwen",
    ],
}
FAMILIES = [
    "claude-fable-5",
    "claude-haiku-5",
    "claude-opus-4.8",
    "claude-sonnet-5",
    "codex",
    "deepseek",
    "gemini",
    "gemma",
    "glm",
    "grok",
    "hy",
    "kimi",
    "longcat",
    "mimo",
    "minimax",
    "muse-glimmer",
    "muse-spark",
    "qwen",
]


def fact(value: Any) -> dict[str, Any]:
    return {"value": value, "method": "reviewed", "evidence": copy.deepcopy(EVIDENCE)}


def grok_facts() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "unit": "families/grok",
        "promptReference": "prompting/xai-grok-prompting-reference.md",
        "runtimeReference": "references/model-prompting.md#xai-grok-45-through-grok-build",
        "models": {
            "grok-4.8": {
                "status": "current",
                "displayName": "Grok 4.8",
                "routes": [{"harness": "grok", "model": "grok-4.8", "register": True}],
                "facts": {
                    "contextWindow": fact(500000),
                    "maxOutput": fact("unlimited"),
                    "effortValues": fact(["low", "medium", "high", "xhigh"]),
                    "effortDefault": fact("high"),
                    "thinkingCanDisable": fact(False),
                },
            },
            "grok-4.5": {
                "status": "current",
                "displayName": "Grok 4.5",
                "routes": [{"harness": "grok", "model": "grok-4.5", "register": True}],
                "facts": {
                    "contextWindow": fact(500000),
                    "maxOutput": fact("unlimited"),
                    "effortValues": fact(["low", "medium", "high"]),
                    "effortDefault": fact("high"),
                    "thinkingCanDisable": fact(False),
                    "effortOnOther": fact({"behaviour": "coerced", "map": {"xhigh": "high"}}),
                },
            },
            "grok-4": {"status": "retired", "facts": {"retires": fact("2026-05-15")}},
        },
        "guidance": [
            {
                "id": "g1",
                "text": "Short, specific rules files are followed more reliably than long ones.",
                "applies": ["*"],
                "class": "harness",
                "source": "vendor-model",
                "locator": "AGENTS.md",
                "surfaces": ["card", "ledger"],
                "reviewedAt": "2026-09-28",
            },
            {
                "id": "g2",
                "text": "Do not rely on xhigh effort here; it runs at high.",
                "applies": ["grok-4.5"],
                "class": "vendor",
                "source": "vendor-model",
                "locator": "Effort levels",
                "surfaces": ["reference"],
                "reviewedAt": "2026-09-28",
            },
        ],
    }


def grok_sources() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "unit": "families/grok",
        "notPublished": [],
        "watch": [],
        "sources": [
            {
                "id": "vendor-model",
                "url": "https://docs.example/grok.md",
                "kind": "vendor",
                "pageType": "model",
                "fetch": "http",
                "format": "markdown",
                "reviewedAt": "2026-09-28",
            }
        ],
    }


class MarkerTests(unittest.TestCase):
    def test_the_body_is_replaced_and_nothing_else_changes(self) -> None:
        text = (
            "intro\n"
            + smf.START_TEXT.format(name="grok")
            + "\nold line\n"
            + smf.END_TEXT.format(name="grok")
            + "\noutro\n"
        )
        result = smf.replace_blocks(text, {"grok": ["- new"]}, KNOWN, "f")
        self.assertEqual(
            "intro\n"
            + smf.START_TEXT.format(name="grok")
            + "\n- new\n"
            + smf.END_TEXT.format(name="grok")
            + "\noutro\n",
            result,
        )

    def test_an_empty_block_keeps_the_pair(self) -> None:
        text = smf.START_TEXT.format(name="kimi") + "\n" + smf.END_TEXT.format(name="kimi") + "\n"
        self.assertEqual(text, smf.replace_blocks(text, {}, KNOWN, "f"))

    def test_malformed_markers_are_refused(self) -> None:
        start, end = smf.START_TEXT.format(name="grok"), smf.END_TEXT.format(name="grok")
        kimi_start, kimi_end = smf.START_TEXT.format(name="kimi"), smf.END_TEXT.format(name="kimi")
        cases = {
            "START has no END": start,
            "END has no START": end,
            "appears twice": f"{start}\n{end}\n{start}\n{end}",
            "does not name a capability card": smf.START_TEXT.format(name="nope")
            + "\n"
            + smf.END_TEXT.format(name="nope"),
            "is followed by MODEL-FACTS:kimi START": f"{start}\n{kimi_start}\n{kimi_end}\n{end}",
        }
        for message, text in cases.items():
            with self.subTest(message), self.assertRaises(smf.GenerateError) as caught:
                smf.replace_blocks(text, {}, KNOWN, "f")
            self.assertIn(message, str(caught.exception))


class BlockTests(unittest.TestCase):
    def test_card_block(self) -> None:
        lines = smf.render_block(grok_facts(), grok_sources(), "card")
        self.assertEqual(
            [
                "- **Models:** `grok-4.8`, `grok-4.5`",
                "- **Context:** 500,000 tokens, no output limit.",
                "- **Effort:** `grok-4.8`: low, medium, high, xhigh (default high), cannot be disabled; "
                "`grok-4.5`: low, medium, high (default high), cannot be disabled.",
                "- **Effort on other values:** `grok-4.5`: xhigh runs as high.",
                "- **Facts checked:** 2026-09-28. Sources: `docs/agents/model-facts/families/grok/FACTS.md`.",
            ],
            lines,
        )

    def test_a_model_its_registered_harness_rejects_is_not_on_the_card(self) -> None:
        facts = grok_facts()
        facts["models"]["grok-4.5"]["routes"][0]["register"] = False
        lines = smf.render_block(facts, grok_sources(), "card")
        self.assertEqual("- **Models:** `grok-4.8`", lines[0])
        self.assertNotIn("grok-4.5", "\n".join(lines))
        sheet = smf.render_sheet("families/grok", facts, grok_sources(), {})
        self.assertIn("| `grok-4.8` | grok | direct | `grok-4.8` |", sheet)
        self.assertIn("| `grok-4.5` | grok | direct | `grok-4.5` |", sheet)

    def test_models_without_a_rejecting_harness_stay_on_the_card(self) -> None:
        facts = grok_facts()
        facts["models"]["grok-4.5"]["routes"] = [
            {"harness": "opencode", "host": "go", "model": "go/grok-4.5"}
        ]
        self.assertEqual(
            "- **Models:** `grok-4.8`, `grok-4.5`",
            smf.render_block(facts, grok_sources(), "card")[0],
        )
        del facts["models"]["grok-4.5"]["routes"]
        self.assertEqual(
            "- **Models:** `grok-4.8`, `grok-4.5`",
            smf.render_block(facts, grok_sources(), "card")[0],
        )

    def test_a_card_only_statement_reaches_the_reference_and_not_the_card(self) -> None:
        facts = grok_facts()
        facts["guidance"] = [
            dict(facts["guidance"][0], id="c1", text="A card-only rule.", surfaces=["card"])
        ]
        text = "- **Stated by harness:** A card-only rule."
        self.assertIn(text, smf.render_block(facts, grok_sources(), "reference"))
        self.assertNotIn(text, smf.render_block(facts, grok_sources(), "card"))
        self.assertNotIn(text, smf.render_block(facts, grok_sources(), "ledger"))

    def test_surfaces_select_guidance(self) -> None:
        reference = smf.render_block(grok_facts(), grok_sources(), "reference")
        self.assertIn(
            "- **Stated by vendor (`grok-4.5`):** Do not rely on xhigh effort here; it runs at high.",
            reference,
        )
        self.assertTrue(any("rules files" in line for line in reference))
        card = smf.render_block(grok_facts(), grok_sources(), "card")
        self.assertFalse(any("Do not rely on xhigh" in line for line in card))

    def test_a_retiring_model_shows_its_date_and_absent_facts_omit_lines(self) -> None:
        facts = {
            "schemaVersion": 1,
            "unit": "families/mimo",
            "guidance": [],
            "models": {
                "mimo-v2.5": {"status": "retiring", "facts": {"retires": fact("2026-10-21")}}
            },
        }
        self.assertEqual(
            ["- **Models:** `mimo-v2.5` (retires 2026-10-21)"],
            smf.render_block(facts, {"sources": []}, "card"),
        )

    def test_no_facts_renders_nothing(self) -> None:
        self.assertEqual([], smf.render_block(None, None, "card"))


class RegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = json.loads((ROOT / REGISTRY).read_text(encoding="utf-8"))

    def test_no_units_changes_nothing(self) -> None:
        self.assertEqual(self.registry, smf.update_registry(self.registry, {}))

    def test_only_listed_keys_change_and_nothing_is_removed(self) -> None:
        facts = grok_facts()
        facts["models"]["grok-4.7"] = {
            "status": "current",
            "displayName": "Grok 4.7",
            "routes": [{"harness": "grok", "model": "grok-4.7", "register": True}],
            "facts": {"effortValues": fact(["low", "medium", "high"])},
        }
        updated = smf.update_registry(self.registry, {"grok": (facts, grok_sources())})
        grok, before = updated["harnesses"]["grok"], self.registry["harnesses"]["grok"]
        self.assertEqual(["low", "medium", "high"], grok["models"]["grok-4.7"]["effortValues"])
        self.assertEqual("model-facts:families/grok", grok["models"]["grok-4.7"]["provenance"])
        self.assertEqual(before["defaultModel"], grok["defaultModel"])
        self.assertEqual(before["effort"], grok["effort"])
        for key, value in before["models"]["grok-4.7"].items():
            if key not in smf.MODEL_KEYS:
                self.assertEqual(value, grok["models"]["grok-4.7"][key], key)
        self.assertNotIn("grok-4", grok["models"], "a retired model is never registered")
        others = {k: v for k, v in updated["harnesses"].items() if k != "grok"}
        self.assertEqual(
            {k: v for k, v in self.registry["harnesses"].items() if k != "grok"}, others
        )

    def test_a_new_current_model_is_registered_with_its_references(self) -> None:
        updated = smf.update_registry(self.registry, {"grok": (grok_facts(), grok_sources())})
        self.assertEqual(
            {
                "displayName": "Grok 4.8",
                "aliases": [],
                "effortValues": ["low", "medium", "high", "xhigh"],
                "promptReference": "docs/prompting/xai-grok-prompting-reference.md",
                "runtimeReference": "references/model-prompting.md#xai-grok-45-through-grok-build",
                "capabilityCard": f"{LEDGER}/grok.md",
                "provenance": "model-facts:families/grok",
            },
            updated["harnesses"]["grok"]["models"]["grok-4.8"],
        )

    def test_a_prompt_reference_named_under_docs_is_registered_as_a_repository_path(self) -> None:
        facts = grok_facts()
        facts["promptReference"] = "prompting/xai-grok-prompting-reference.md"
        updated = smf.update_registry(self.registry, {"grok": (facts, grok_sources())})
        self.assertEqual(
            "docs/prompting/xai-grok-prompting-reference.md",
            updated["harnesses"]["grok"]["models"]["grok-4.8"]["promptReference"],
        )

    def test_registering_without_references_is_refused(self) -> None:
        facts = grok_facts()
        del facts["promptReference"]
        with self.assertRaises(smf.GenerateError):
            smf.update_registry(self.registry, {"grok": (facts, grok_sources())})

    def test_family_fields_come_from_unit_facts(self) -> None:
        facts = {
            "schemaVersion": 1,
            "unit": "families/gemma",
            "guidance": [],
            "models": {},
            "facts": {
                "contextWindow": fact(262144),
                "samplingDefaults": fact({"temperature": 1.0, "top_p": 0.95, "top_k": 64}),
                "license": fact({"name": "Apache-2.0", "url": None}),
                "effortValues": fact(["on", "off"]),
            },
        }
        sources = {
            "sources": [
                {
                    "id": "hf",
                    "url": "https://huggingface.co/google/gemma-4-31B-it",
                    "fetch": "huggingface",
                }
            ]
        }
        family = smf.update_registry(self.registry, {"gemma": (facts, sources)})["modelFamilies"][
            "gemma-4"
        ]
        self.assertEqual(262144, family["contextWindow"])
        self.assertEqual("Apache-2.0", family["license"])
        # The reasoning control says how effort is passed, which no fact records; it is never generated.
        self.assertEqual(
            self.registry["modelFamilies"]["gemma-4"]["reasoningControl"],
            family["reasoningControl"],
        )
        self.assertEqual("https://huggingface.co/google/gemma-4-31B-it", family["modelCardUrl"])
        self.assertEqual(
            self.registry["modelFamilies"]["gemma-4"]["reasoningControl"]["kind"],
            family["reasoningControl"]["kind"],
        )


class HarnessAndEffortTests(unittest.TestCase):
    def test_any_other_value(self) -> None:
        model = {"facts": {"effortOnOther": fact({"behaviour": "coerced", "map": {"*": "max"}})}}
        self.assertEqual("any other value runs as max", smf._effort_other(model))

    def test_harness_statements_reach_families_registered_through_that_harness(self) -> None:
        statement = {
            "id": "h1",
            "text": "The sandbox is off unless the dispatch turns it on.",
            "applies": ["*"],
            "class": "harness",
            "source": "grok-sandbox",
            "locator": "Profiles",
            "surfaces": ["card"],
            "reviewedAt": "2026-09-28",
        }
        self.assertEqual({"grok"}, smf.registered_harnesses(grok_facts()))
        lines = smf.render_block(grok_facts(), grok_sources(), "reference", [statement])
        self.assertIn(
            "- **Stated by harness:** The sandbox is off unless the dispatch turns it on.", lines
        )

    def test_an_explicitly_empty_route_effort_list_clears_the_registry_value(self) -> None:
        registry = json.loads((ROOT / REGISTRY).read_text(encoding="utf-8"))
        facts = {
            "schemaVersion": 1,
            "unit": "families/kimi",
            "guidance": [],
            "models": {
                "kimi-k3": {
                    "routes": [
                        {
                            "harness": "kimi",
                            "model": "kimi-code/k3",
                            "register": True,
                            "effortValues": [],
                        }
                    ],
                    "facts": {"effortValues": fact(["low", "high", "max"])},
                }
            },
        }
        updated = smf.update_registry(registry, {"kimi": (facts, {"sources": []})})
        self.assertEqual([], updated["harnesses"]["kimi"]["models"]["kimi-code/k3"]["effortValues"])

    def test_harness_and_host_units_get_a_facts_sheet(self) -> None:
        facts = {
            "schemaVersion": 1,
            "unit": "harnesses/grok",
            "guidance": [],
            "facts": {"sandboxDefault": fact("off")},
        }
        sources = {
            "sources": [
                {
                    "id": "vendor-model",
                    "url": "https://docs.example/sandbox.md",
                    "kind": "harness",
                    "pageType": "harness",
                }
            ],
            "notPublished": [],
        }
        sheet = smf.render_sheet("harnesses/grok", facts, sources, {})
        self.assertIn('| sandboxDefault | `"off"` | vendor-model |', sheet)
        self.assertNotIn("## Routes", sheet)


class SheetTests(unittest.TestCase):
    def test_a_host_value_overrides_the_vendor_value_on_its_route(self) -> None:
        facts = grok_facts()
        facts["models"]["grok-4.8"]["routes"].append(
            {"harness": "opencode", "host": "go", "model": "go/grok-4.8"}
        )
        hosts = {
            "go": {
                "models": {
                    "go/grok-4.8": {
                        "of": "families/grok#grok-4.8",
                        "facts": {"contextWindow": fact(256000), "webSearch": fact(False)},
                    }
                }
            }
        }
        sheet = smf.render_sheet("families/grok", facts, grok_sources(), hosts)
        self.assertIn(
            "| `grok-4.8` | grok | direct | `grok-4.8` | 500,000 tokens, no output limit |", sheet
        )
        self.assertIn(
            "| `grok-4.8` | opencode | go | `go/grok-4.8` | 256,000 tokens, no output limit |",
            sheet,
        )
        self.assertIn("| no |", sheet)
        self.assertIn("<https://docs.example/grok.md>", sheet)


class RepositoryTests(unittest.TestCase):
    def test_every_family_has_its_markers(self) -> None:
        self.assertEqual(FAMILIES, smf.family_names(ROOT))
        for host, names in CARD_MARKERS.items():
            skill = (ROOT / SKILL.format(host=host)).read_text(encoding="utf-8")
            self.assertEqual(
                names,
                sorted(m["name"] for m in smf.MARKER.finditer(skill) if m["edge"] == "START"),
                host,
            )
            reference = (ROOT / REFERENCE.format(host=host)).read_text(encoding="utf-8")
            self.assertEqual(
                FAMILIES,
                sorted(m["name"] for m in smf.MARKER.finditer(reference) if m["edge"] == "START"),
            )
        for name in FAMILIES:
            self.assertIn(
                smf.START_TEXT.format(name=name),
                (ROOT / LEDGER / f"{name}.md").read_text(encoding="utf-8"),
            )

    def test_generated_outputs_are_current(self) -> None:
        self.assertEqual([], smf.synchronize(ROOT, check=True))

    def test_check_reports_drift_and_write_repairs_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for relative in [
                REGISTRY,
                *[SKILL.format(host=h) for h in HOSTS],
                *[REFERENCE.format(host=h) for h in HOSTS],
            ]:
                (root / relative).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(ROOT / relative, root / relative)
            shutil.copytree(ROOT / LEDGER, root / LEDGER)
            unit = root / "docs/agents/model-facts" / "families" / "grok"
            unit.mkdir(parents=True)
            (unit / "facts.json").write_text(json.dumps(grok_facts()), encoding="utf-8")
            (unit / "sources.json").write_text(json.dumps(grok_sources()), encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(1, smf.main(["--check"], root=root))
                self.assertEqual(0, smf.main([], root=root))
                self.assertEqual(0, smf.main(["--check"], root=root))
            self.assertIn("stale docs/agents/model-facts/families/grok/FACTS.md", err.getvalue())
            skill = (root / SKILL.format(host="claude")).read_text(encoding="utf-8")
            self.assertIn("- **Models:** `grok-4.8`, `grok-4.5`", skill)
            self.assertNotIn(
                "grok-4.8",
                (root / SKILL.format(host="claude"))
                .read_text(encoding="utf-8")
                .split(smf.START_TEXT.format(name="grok"))[0],
            )


if __name__ == "__main__":
    unittest.main()
