"""Every harness must appear on every surface (the qwen 0.7.0 gap must not recur)."""

from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path

from pitwall.agents import (
    doctor,
)
from pitwall.agents.harnesses import adapter_ids
from pitwall.agents.installation import (
    shim_names,
    shim_script,
)
from pitwall.agents.resources import (
    read_resource_json,
)
from tests.agents import (
    shim_test_support,
    test_shim_contract,
)

ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = ROOT

CLAUDE = ROOT / "plugins" / "claude"
CODEX_SKILL = ROOT / "plugins" / "codex" / "skills" / "subagent-model-routing" / "SKILL.md"
COPILOT_SKILL = ROOT / "plugins" / "copilot" / "skills" / "subagent-model-routing" / "SKILL.md"


class ParityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ids = sorted(adapter_ids())
        self.registry = read_resource_json("config/harness-registry.json")
        self.installers = read_resource_json("config/harness-installers.json")
        self.distill = (CLAUDE / "commands" / "distill.md").read_text(encoding="utf-8")
        self.claude_skill = (CLAUDE / "skills" / "subagent-model-routing" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        hooks = CLAUDE / "hooks"
        # The dag tripwire keeps its shim tables in a sibling module.
        self.hooks = [
            (hooks / "dag-tripwire.py").read_text(encoding="utf-8")
            + (hooks / "dag_tripwire_shims.py").read_text(encoding="utf-8"),
            (hooks / "ledger-tripwire.py").read_text(encoding="utf-8"),
        ]

    def test_every_harness_is_on_every_surface(self) -> None:
        for harness_id in self.ids:
            with self.subTest(harness=harness_id):
                shim = shim_test_support.SHIMS[harness_id]
                self.assertTrue(
                    shim.is_file() and shim.stat().st_mode & 0o111,
                    "generated shim missing or not executable",
                )
                self.assertEqual(shim_script(f"{harness_id}-shim.sh"), shim.read_text())
                self.assertIn(f"{harness_id}-shim.sh", shim_names())
                self.assertIn(f"{harness_id}-shim.sh", doctor.INSTALL_ENTRYPOINTS)
                self.assertIn(harness_id, doctor.HARNESS_HELP)
                self.assertIn(harness_id, self.installers["harnesses"])
                self.assertIn(harness_id, shim_test_support.SHIMS)
                self.assertIn(harness_id, test_shim_contract.HARNESS_ARGS)
                self.assertIn(f'`shim:"{harness_id}"`', self.distill)
                for hook in self.hooks:
                    self.assertIn(f'"{harness_id}-shim"', hook)
                    self.assertIn(f'"pitwall:{harness_id}-shim"', hook)
                    # the id must be one whole alternative of the shim-basename group, wherever it sits
                    self.assertRegex(
                        hook,
                        rf"\((?:\?:)?(?:[^)|]+\|)*{re.escape(harness_id)}(?:\|[^)|]+)*\)-shim",
                    )
                if harness_id != "claude":
                    self.assertTrue((CLAUDE / "agents" / f"{harness_id}-shim.md").is_file())
                    self.assertIn(f"~/.claude/scripts/{harness_id}-shim.sh", self.claude_skill)
                    self.assertIn(
                        f"~/.claude/scripts/{harness_id}-shim.sh",
                        COPILOT_SKILL.read_text(encoding="utf-8"),
                    )
                if harness_id != "codex":
                    self.assertIn(
                        f"~/.claude/scripts/{harness_id}-shim.sh",
                        CODEX_SKILL.read_text(encoding="utf-8"),
                    )

    def test_route_shim_is_on_the_shared_surfaces(self) -> None:
        self.assertIn("route-shim.sh", doctor.INSTALL_ENTRYPOINTS)
        for hook in self.hooks:
            self.assertIn('"pitwall:route-shim"', hook)

    def test_client_native_boundaries_match_each_host(self) -> None:
        codex = CODEX_SKILL.read_text(encoding="utf-8")
        claude = self.claude_skill
        copilot = COPILOT_SKILL.read_text(encoding="utf-8")

        for shim in ("claude", "kimi"):
            self.assertIn(f"~/.claude/scripts/{shim}-shim.sh", codex)
        self.assertNotIn("~/.claude/scripts/codex-shim.sh", codex)
        self.assertIn("native Codex harness", codex)

        for shim in ("codex", "kimi"):
            self.assertIn(f"~/.claude/scripts/{shim}-shim.sh", claude)
        self.assertNotIn("~/.claude/scripts/claude-shim.sh", claude)
        self.assertIn("native `Agent` calls for Claude work", claude)

        for shim in ("codex", "claude", "kimi"):
            self.assertIn(f"~/.claude/scripts/{shim}-shim.sh", copilot)

    def test_every_host_loads_the_machine_capability_inventory(self) -> None:
        expected = "pitwall/agents/harness-capabilities.md"
        for skill in (
            self.claude_skill,
            CODEX_SKILL.read_text(encoding="utf-8"),
            COPILOT_SKILL.read_text(encoding="utf-8"),
        ):
            self.assertIn(expected, skill)
            self.assertIn("availability context", skill)
            self.assertIn("setup inventory", skill)

    def test_prompting_references_and_host_bundles_stay_aligned(self) -> None:
        skill_dirs = [
            ROOT / "plugins" / package / "skills/subagent-model-routing"
            for package in ("claude", "codex", "copilot")
        ]
        bundle_hashes: set[str] = set()
        for skill_dir in skill_dirs:
            skill = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
            bundle = skill_dir / "references/model-prompting.md"
            bundle_hashes.add(hashlib.sha256(bundle.read_bytes()).hexdigest())
            self.assertNotRegex(bundle.read_text(encoding="utf-8"), r"(?im)^#{1,6}\s+.*mythos")
            self.assertNotIn("Full reference: `docs/prompting/", skill)
            for reference in re.findall(r"Full reference: `([^`]+)`", skill):
                relative, _, anchor = reference.partition("#")
                target = skill_dir / relative
                self.assertTrue(target.is_file(), target)
                if anchor:
                    self.assertIn(
                        f"(#{anchor})",
                        target.read_text(encoding="utf-8").lower(),
                    )
        self.assertEqual(1, len(bundle_hashes))

        cases = (
            ("sonnet-5", "claude-sonnet-55"),
            ("opus-4.8", "claude-opus-55"),
            ("fable-5", "claude-fable-51"),
            ("haiku-5", "claude-haiku-55"),
        )
        for slug, anchor in cases:
            reference = ROOT / f"docs/prompting/anthropic-claude-{slug}-prompting-reference.md"
            card = CLAUDE / f"skills/subagent-model-routing/ledger/claude-{slug}.md"
            sources = json.loads(
                (ROOT / f"docs/agents/model-facts/families/claude-{slug}/sources.json").read_text(
                    encoding="utf-8"
                )
            )
            guidance = [
                item["url"] for item in sources["sources"] if item["pageType"] == "guidance"
            ]
            self.assertTrue(reference.is_file() and card.is_file())
            self.assertTrue(guidance)
            for url in guidance:
                self.assertIn(url, reference.read_text(encoding="utf-8"))
            self.assertIn(
                f"references/model-prompting.md#{anchor}",
                CODEX_SKILL.read_text(encoding="utf-8"),
            )
            self.assertIn(
                f"references/model-prompting.md#{anchor}",
                COPILOT_SKILL.read_text(encoding="utf-8"),
            )
            self.assertIn(
                f"../references/model-prompting.md#{anchor}",
                card.read_text(encoding="utf-8"),
            )

    def test_channel_discipline_is_taught_everywhere(self) -> None:
        skills = {
            "claude": self.claude_skill,
            "codex": CODEX_SKILL.read_text(encoding="utf-8"),
            "copilot": COPILOT_SKILL.read_text(encoding="utf-8"),
        }
        heading = "## Asking and steering through the orchestrator channel"
        for host, whole in skills.items():
            with self.subTest(host=host):
                self.assertIn(heading, whole)
                text = whole.split(heading, 1)[1].split("\n## ", 1)[0]
                self.assertIn("pitwall agents setup mcp", text)
                for needle in (
                    "dispatch_and_wait",
                    "answer_and_wait",
                    "--routing-ask-support",
                    "--routing-workspace isolated",
                    "pitwall agents inbox",
                    "pitwall agents answer",
                    "pitwall agents runs resume",
                    "dangerous and irreversible",
                    "pitwall agents steer",
                    "pitwall agents runs stop",
                ):
                    self.assertIn(needle, text)
        for bundle in ROOT.glob(
            "plugins/*/skills/subagent-model-routing/references/model-prompting.md"
        ):
            self.assertIn(
                "## Asking through the orchestrator channel", bundle.read_text(encoding="utf-8")
            )
        for reference in sorted(ROOT.glob("docs/prompting/*-prompting-reference.md")):
            with self.subTest(reference=reference.name):
                self.assertIn(
                    "## Asking through the orchestrator channel",
                    reference.read_text(encoding="utf-8"),
                )

    def test_distillation_and_model_provenance_contracts_remain_explicit(self) -> None:
        for text in (
            "PITWALL_AGENTS_HOME",
            "PITWALL_AGENTS_HOME",
            "set both `ROOT` and `COMPONENT_ROOT` to `<git-top>`",
            "development Pitwall clone",
            "bootstrap updates refuse that payload",
            "Never edit an installed plugin cache",
            "Map each record by both `shim` and `model`",
            '`shim:"codex"`',
            '`shim:"claude"`',
            '`shim:"grok"`',
            '`shim:"kimi"`',
            '`shim:"opencode"`',
            "kimi-for-coding/*",
            "zai-coding-plan/*",
            'git -C "$ROOT" diff',
        ):
            self.assertIn(text, self.distill)

        codex_reference = (
            ROOT / "docs/prompting/openai-codex-gpt-prompting-reference.md"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "Selector provenance: current Codex runtime model metadata",
            codex_reference,
        )
        for base in (
            ROOT / "docs/agents/routing-readme.md",
            ROOT / "plugins",
            ROOT / "docs/prompting",
        ):
            paths = (
                [base] if base.is_file() else [path for path in base.rglob("*") if path.is_file()]
            )
            for path in paths:
                self.assertNotIn(
                    "`gpt-5.6`", path.read_text(encoding="utf-8", errors="ignore"), path
                )

    def test_doctor_schema_enumerates_every_harness(self) -> None:
        schema = read_resource_json("schemas/doctor-result.schema.json")
        harness_enum = schema["properties"]["checks"]["items"]["properties"]["provider"]["enum"]
        filter_enum = schema["properties"]["modes"]["properties"]["providerFilter"]["enum"]
        for harness_id in self.ids:
            with self.subTest(harness=harness_id):
                self.assertIn(harness_id, harness_enum)
                self.assertIn(harness_id, filter_enum)
        self.assertIn(None, filter_enum)


if __name__ == "__main__":
    unittest.main()
