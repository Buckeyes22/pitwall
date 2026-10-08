from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.release

ROOT = Path(__file__).resolve().parents[2]


def test_dependabot_covers_every_pinned_ecosystem() -> None:
    config = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text(encoding="utf-8"))
    assert config["version"] == 2
    seen = {(u["package-ecosystem"], u["directory"]) for u in config["updates"]}
    assert seen == {
        ("uv", "/"),
        ("github-actions", "/"),
        ("docker", "/docker"),
        ("docker-compose", "/"),
        ("npm", "/tools/pi-deps"),
    }
    for update in config["updates"]:
        assert update["groups"], update["package-ecosystem"]
