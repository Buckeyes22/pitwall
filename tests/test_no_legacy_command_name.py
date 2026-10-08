"""The standalone command name and marker text survive only in the migration tables."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_TABLES = {
    "src/pitwall/agents/migrate.py",
    "src/pitwall/agents/migrate_env.py",
}
# Assembled from parts so this file itself carries no legacy name.
_OLD = "subagent-model-" + "routing"
LEGACY = re.compile(
    "|".join(
        (
            "pitwall-agent-" + "routing",
            f"managed by {_OLD}",
            f"{_OLD}: ",
            f"{_OLD}-(?:local|harness-setup)",
            f"{_OLD} (?:doctor|runtime|plugin|ledger|LEDGER)",
            f"Buckeyes22/{_OLD}",
        )
    )
)
# The plugin skill directory and skill id stay `subagent-model-routing`; that name alone is not
# the legacy command, so only the phrases above (command, markers, marketplace, prose) are refused.


def _tracked(*roots: str) -> list[str]:
    output = subprocess.run(
        ["git", "ls-files", "-z", "--", *roots],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout
    return [name for name in output.decode().split("\0") if name]


def test_old_command_name_only_in_migration_tables() -> None:
    hits: list[str] = []
    for name in _tracked("src", "plugins", "tools"):
        if name in MIGRATION_TABLES:
            continue
        path = ROOT / name
        try:
            text = path.read_text(encoding="utf-8")
        except OSError, UnicodeDecodeError:
            continue
        hits.extend(
            f"{name}:{number}: {line.strip()[:100]}"
            for number, line in enumerate(text.splitlines(), 1)
            if LEGACY.search(line)
        )
    assert hits == []


def test_migration_tables_still_carry_the_old_names() -> None:
    joined = "\n".join((ROOT / name).read_text(encoding="utf-8") for name in MIGRATION_TABLES)
    assert "pitwall-agent-" + "routing" in joined
    assert f"# managed by {_OLD}" in joined
