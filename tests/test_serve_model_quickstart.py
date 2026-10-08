import argparse
import re
import shlex
from pathlib import Path

import pytest

from pitwall.agents import cli as cli_agents
from pitwall.cli import models as cli_models
from pitwall.cli import serve_model as cli_serve_model

QUICKSTART = Path(__file__).resolve().parents[1] / "docs/operator/serve-quickstart.md"
# Typed placeholders the argument parsers would otherwise reject.
_PLACEHOLDERS = {
    "<ttl-minutes>": "120",
    "<gpu-count>": "1",
    "<container-disk-gb>": "20",
    "<engine>": "vllm",
    "<usd-per-second>": "0.001",
}


def _sections(text: str) -> dict[str, str]:
    """Heading title -> body, for every ``##``/``###`` heading, in document order."""
    matches = list(re.finditer(r"^#{2,3} (.+)$", text, flags=re.MULTILINE))
    return {
        match.group(1): text[match.end() : matches[i + 1].start() if i + 1 < len(matches) else None]
        for i, match in enumerate(matches)
    }


def _bash_commands(body: str) -> list[list[str]]:
    """Every fenced ``bash`` command in *body*, as argv tokens (``\\`` continuations joined)."""
    return [
        shlex.split(block.partition("```")[0].replace("\\\n", " "))
        for block in body.split("```bash")[1:]
    ]


def _pitwall_args(body: str, group: str) -> list[argparse.Namespace]:
    """Parse each ``pitwall <group> ...`` command in *body* with the real CLI parser."""
    parsed = []
    for tokens in _bash_commands(body):
        if "pitwall" not in tokens:
            continue
        argv = tokens[tokens.index("pitwall") + 1 :]
        if argv[0] != group:
            continue
        argv = [_PLACEHOLDERS.get(value, value) for value in argv]
        parsed.append(
            cli_models._parse_models_args(argv[1:])
            if group == "models"
            else cli_serve_model._parse_serve_model_args(argv[1:])
        )
    return parsed


def _curl_urls(body: str) -> list[str]:
    return [
        token
        for tokens in _bash_commands(body)
        if "curl" in tokens
        for token in tokens
        if token.startswith("<pitwall-api-url>/")
    ]


def test_serve_model_quickstart_walks_catalogue_to_stop_in_order() -> None:
    titles = list(_sections(QUICKSTART.read_text(encoding="utf-8")))
    flow = ["Catalogue", "Fit", "Dry run", "Serve", "Verify `/v1/models`", "Verify chat", "Stop"]

    assert [title for title in titles if title in flow] == flow


def test_quickstart_sections_carry_the_commands_that_belong_to_them() -> None:
    sections = _sections(QUICKSTART.read_text(encoding="utf-8"))

    (listing,) = _pitwall_args(sections["Catalogue"], "models")
    assert listing.command == "list"
    (fit,) = _pitwall_args(sections["Fit"], "models")
    assert fit.command == "fit"
    assert fit.model == "<model>"

    plan, dry_run = _pitwall_args(sections["Dry run"], "serve")
    assert plan.plan_only and not plan.dry_run and plan.capability is None
    assert dry_run.dry_run and not dry_run.plan_only and dry_run.capability == "<capability>"

    (live,) = _pitwall_args(sections["Serve"], "serve")
    assert live.capability == "<capability>"
    assert not live.plan_only and not live.dry_run

    assert _curl_urls(sections["Verify `/v1/models`"]) == [
        "<pitwall-api-url>/v1/openai/<capability>/v1/models"
    ]
    assert _curl_urls(sections["Verify chat"]) == [
        "<pitwall-api-url>/v1/openai/<capability>/v1/chat/completions"
    ]


def test_quickstart_route_recovery_command_parses_with_the_agents_cli() -> None:
    serve_body = _sections(QUICKSTART.read_text(encoding="utf-8"))["Serve"]
    (recovery,) = [
        tokens[tokens.index("pitwall") + 1 :]
        for tokens in _bash_commands(serve_body)
        if tokens[:3] == ["pitwall", "agents", "profiles"]
    ]

    args = cli_agents.build_parser().parse_args(recovery[1:])

    assert (args.command, args.routes_command) == ("profiles", "add")
    assert args.from_pitwall == "<capability>"


def test_serve_model_quickstart_stop_names_lease_mutate_scope() -> None:
    sections = _sections(QUICKSTART.read_text(encoding="utf-8"))

    assert "lease:mutate" in sections["Stop"]
    assert _curl_urls(sections["Stop"]) == ["<pitwall-api-url>/v1/leases/<lease-id>/stop"] * 2


def _documented_pitwall_argv(text: str) -> list[list[str]]:
    commands: list[list[str]] = []
    for block in text.split("```bash")[1:]:
        command, _, _ = block.partition("```")
        tokens = shlex.split(command.replace("\\\n", " "))
        if "pitwall" in tokens:
            commands.append(tokens[tokens.index("pitwall") + 1 :])
    return commands


def _parse_documented_command(argv: list[str]) -> None:
    argv = [_PLACEHOLDERS.get(value, value) for value in argv]
    if argv[0] == "models":
        cli_models._parse_models_args(argv[1:])
    elif argv[0] == "serve":
        cli_serve_model._parse_serve_model_args(argv[1:])


def test_every_fenced_quickstart_pitwall_command_parses() -> None:
    commands = _documented_pitwall_argv(QUICKSTART.read_text(encoding="utf-8"))

    assert commands
    for command in commands:
        _parse_documented_command(command)


def test_documented_command_parser_rejects_a_bogus_flag_in_fixture_copy() -> None:
    text = QUICKSTART.read_text(encoding="utf-8").replace(
        "models list", "models list --bogus-flag", 1
    )
    commands = _documented_pitwall_argv(text)

    with pytest.raises(SystemExit):
        _parse_documented_command(commands[0])
