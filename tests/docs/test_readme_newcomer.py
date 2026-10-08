"""The README path works for someone with no prior Pitwall state."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
README = (ROOT / "README.md").read_text(encoding="utf-8")
BLOCKS = re.findall(r"```bash\n(.*?)```", README, flags=re.S)


def test_install_uses_the_release_wheel() -> None:
    assert (
        "uv tool install --python 3.14.7 https://github.com/Buckeyes22/pitwall/releases/download/"
        "v0.3.0a1/pitwall-0.3.0a1-py3-none-any.whl"
    ) in README


def test_every_broker_terminal_loads_the_same_configuration() -> None:
    needs_config = [b for b in BLOCKS if "pitwall-api" in b or "pitwall db migrate" in b]
    assert len(needs_config) >= 2
    for block in needs_config:
        assert "cd pitwall" in block
        assert "set -a; . ./.env.quickstart.local; set +a" in block


def test_dispatch_examples_run_from_path_with_a_shipped_prompt() -> None:
    assert "pitwall agents dispatch codex examples/prompts/first-dispatch.md" in README
    assert "codex-shim.sh prompt.md" not in README
    assert (ROOT / "examples/prompts/first-dispatch.md").is_file()


def test_disclosures_precede_the_first_dispatch_and_serve() -> None:
    first_dispatch = README.index("pitwall agents dispatch")
    first_serve = README.index("pitwall serve")
    assert README.index("PITWALL_AGENTS_UNRESTRICTED=1") < first_dispatch
    assert README.index("## Where your data goes") < first_dispatch
    assert README.index("RUNPOD_API_KEY") < first_serve
    assert "before any paid call goes out" not in README


def test_non_affiliation_covers_named_providers() -> None:
    legal = README.split("### Trademark and Non-Affiliation", 1)[1]
    for name in ("RunPod", "OpenAI", "Anthropic", "Google", "GitHub"):
        assert name in legal
    assert "interoperab" in legal
