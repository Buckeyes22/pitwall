"""docs/agents shows exactly what the registrar writes and what doctor checks (plan Task 6)."""

from __future__ import annotations

import re
from pathlib import Path

from pitwall.mcp_install import SCOPES, render_snippet

ROOT = Path(__file__).resolve().parents[2]
AGENTS = ROOT / "docs" / "agents"
PLACEHOLDER = ["/path/to/pitwall", "mcp", "serve", "broker"]
MARKER = re.compile(
    r"<!-- pitwall-mcp-install: (?P<harness>[a-z-]+) (?P<scope>user|project) -->\n```[a-z]*\n(?P<body>.*?)\n```",
    re.S,
)
DOCTOR_IDS = (
    "install.python",
    "config.file",
    "personal.runpod_credential",
    "personal.endpoint_key",
    "personal.routing_cli",
    "personal.leases",
    "registry.mode",
    "config.runtime",
    "db.connect",
    "db.migrations",
    "registry.capabilities",
    "registry.providers",
    "redis.connect",
    "spend.budget",
    "spend.kill_switch",
    "spend.burn_rate",
    "api.health",
    "canary.dry_run",
)


def _blocks() -> dict[tuple[str, str], str]:
    found: dict[tuple[str, str], str] = {}
    for page in AGENTS.glob("*.md"):
        for match in MARKER.finditer(page.read_text(encoding="utf-8")):
            found[(match["harness"], match["scope"])] = match["body"]
    return found


def test_every_harness_and_scope_is_documented_verbatim() -> None:
    blocks = _blocks()
    for harness, scopes in SCOPES.items():
        for scope in scopes:
            assert blocks.get((harness, scope)) == render_snippet(harness, scope, PLACEHOLDER), (
                harness,
                scope,
            )  # type: ignore[arg-type]  # reason: harness and scope come from a str-keyed table, not the Literal types


def test_install_walkthrough_covers_every_doctor_check_and_the_verify_steps() -> None:
    text = (AGENTS / "install.md").read_text(encoding="utf-8")
    for check_id in DOCTOR_IDS:
        assert f"`{check_id}`" in text, check_id
    for command in (
        "pitwall doctor",
        "pitwall mcp install --dry-run",
        "pitwall db migrate",
        "pitwall setup",
    ):
        assert command in text, command


def test_readme_points_agents_at_the_guide() -> None:
    assert "docs/agents/install.md" in (ROOT / "README.md").read_text(encoding="utf-8")
