"""The agents package carries no legacy variable, module, or file names outside the migration table."""

from __future__ import annotations

import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "src" / "pitwall" / "agents"
MIGRATION_MODULES = {"migrate_env.py", "migrate.py"}
# Assembled from parts so this file itself carries no legacy name.
LEGACY = re.compile(
    "|".join(
        (
            "SUBAGENT_MODEL_" + "ROUTING",
            "PITWALL_AGENT_" + "ROUTING",
            "SHIM_TIMEOUT_" + "SECS",
            "model_" + "routing",
            r"routes" + r"\.json",
        )
    )
)
TEXT_SUFFIXES = {".py", ".sh", ".json", ".md", ".toml", ".txt", ""}


def test_no_legacy_identifiers_remain() -> None:
    hits = [
        f"{path.relative_to(PACKAGE)}:{number}: {line.strip()[:100]}"
        for path in sorted(PACKAGE.rglob("*"))
        if path.is_file()
        and path.name not in MIGRATION_MODULES
        and path.suffix in TEXT_SUFFIXES
        and "__pycache__" not in path.parts
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if LEGACY.search(line)
    ]
    assert hits == []


def test_renamed_modules_replace_the_old_ones() -> None:
    for old in (
        "providers",
        "routes.py",
        "route_sync.py",
        "route_probe.py",
        "routes_setup.py",
        "endpoint_slots.py",
        "endpoint_discovery.py",
        "provider_setup.py",
        "pitwall.py",
        "pitwall_sync.py",
    ):
        path = PACKAGE / old
        # A developer checkout can keep an untracked ``__pycache__`` in a renamed-away
        # package directory; only source files mean the old module is still present.
        if path.is_dir():
            assert not list(path.rglob("*.py")), old
        else:
            assert not path.exists(), old
    for new in ("harnesses", "profiles.py", "endpoints.py", "setup.py", "broker.py"):
        assert (PACKAGE / new).exists(), new
