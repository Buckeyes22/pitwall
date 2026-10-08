from __future__ import annotations

import re
import shlex
from pathlib import Path

from pitwall.cli import capabilities as cli_capabilities
from pitwall.cli import serve_model as cli_serve_model
from pitwall.config import PitwallSettings

GUIDE = Path(__file__).parents[1] / "docs/operator/self-hosted-endpoint.md"
LIVE_ONLY = {
    "PITWALL_SELFHOSTED_BASE_URL",
    "PITWALL_SELFHOSTED_API_KEY_ENV",
}
PRIVATE_ADDRESS = re.compile(
    r"(?:^|[^0-9])(?:"
    r"10(?:\.\d{1,3}){3}|"
    r"192\.168(?:\.\d{1,3}){2}|"
    r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}|"
    r"100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])(?:\.\d{1,3}){2}"
    r")(?:$|[^0-9])|(?:^|[.])ts\.net(?:$|[/:])",
    re.IGNORECASE,
)


def _bash_blocks(text: str) -> list[str]:
    return [block.partition("```")[0] for block in text.split("```bash")[1:]]


def _pitwall_commands(text: str) -> list[list[str]]:
    commands: list[list[str]] = []
    for block in _bash_blocks(text):
        for command in block.replace("\\\n", " ").splitlines():
            tokens = shlex.split(command)
            if "pitwall" not in tokens:
                continue
            start = tokens.index("pitwall") + 1
            commands.append(tokens[start:])
    return commands


def _parse_pitwall(argv: list[str]) -> None:
    replacements = {
        "<seed-file>": "self-hosted.yaml",
        "<capability>": "llm.local",
    }
    argv = [replacements.get(token, token) for token in argv]
    parsers = {
        "seed": cli_capabilities._parse_seed_args,
        "serve": cli_serve_model._parse_serve_model_args,
    }
    parsers[argv[0]](argv[1:])


def _documented_environment_variables(text: str) -> set[str]:
    return set(re.findall(r"`(PITWALL_[A-Z0-9_]+)`", text))


def _setting_name(environment_name: str) -> str:
    return environment_name.lower()


def test_selfhosted_guide_covers_required_operator_warnings() -> None:
    text = GUIDE.read_text(encoding="utf-8")
    required = (
        "## Tool-calling launch flags",
        "--enable-auto-tool-choice",
        "--tool-call-parser <family>",
        "## Provider profile",
        "openai-models",
        "weak",
        "## Runaway consumers",
        "parallel",
        "serial retry loop",
        "## Agent context",
        "PITWALL_SELFHOSTED_BASE_URL",
        "PITWALL_SELFHOSTED_API_KEY_ENV",
    )
    assert all(item in text for item in required)


def test_every_documented_pitwall_command_parses() -> None:
    commands = _pitwall_commands(GUIDE.read_text(encoding="utf-8"))

    assert commands
    for command in commands:
        _parse_pitwall(command)


def test_documented_environment_variables_have_runtime_owners() -> None:
    documented = _documented_environment_variables(GUIDE.read_text(encoding="utf-8"))
    expected = LIVE_ONLY | {
        "PITWALL_SATURATION_WINDOW_S",
        "PITWALL_SATURATION_4XX_THRESHOLD",
        "PITWALL_ENDPOINT_PROBE_TIMEOUT_S",
    }

    assert documented == expected
    settings = documented - LIVE_ONLY
    assert {_setting_name(name) for name in settings} <= set(PitwallSettings.model_fields)


def test_operator_guide_contains_no_private_address() -> None:
    for number, line in enumerate(GUIDE.read_text(encoding="utf-8").splitlines(), start=1):
        assert PRIVATE_ADDRESS.search(line) is None, f"private address on line {number}"
