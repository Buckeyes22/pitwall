"""Walk every CLI parser and record where each argparse surface was actually registered.

Run as ``python -m tests.release.cli_explorer`` (J36 runs it in a subprocess so its argparse
instrumentation never touches the test process). It opens ``--help`` for every command path
of ``pitwall`` (every group in its dispatch table, including ``agents`` and ``usage``),
following each usage line's subcommand group,
and prints one JSON document:

- ``helps``: ``{"pitwall": {command path: {"code": exit code, "help": text}}}``
- ``events``: parser, subparsers, and subcommand registrations as ``[operation, file, line,
  name]``
- ``arguments``: every ``add_argument`` call as ``[file, line, option strings, parser help]``

Nothing here executes a command body: ``--help`` exits before any handler runs, and the two
parsers that are built only after dispatch are opened directly.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
_SUBCOMMANDS = re.compile(r"\{([a-z0-9_,:\-]+)\} \.\.\.")

events: list[list[Any]] = []
arguments: list[tuple[str, int, tuple[str, ...], argparse.ArgumentParser]] = []


def _site(depth: int) -> tuple[str, int]:
    frame = sys._getframe(depth)
    while frame is not None and frame.f_code.co_filename.endswith("argparse.py"):
        frame = frame.f_back
    assert frame is not None
    path = Path(frame.f_code.co_filename).resolve()
    try:
        return str(path.relative_to(ROOT)), frame.f_lineno
    except ValueError:
        return str(path), frame.f_lineno


def _instrument() -> None:
    add_argument = argparse._ActionsContainer.add_argument
    parser_init = argparse.ArgumentParser.__init__
    add_subparsers = argparse.ArgumentParser.add_subparsers
    add_parser = argparse._SubParsersAction.add_parser
    add_group = argparse.ArgumentParser.add_argument_group
    add_exclusive = argparse.ArgumentParser.add_mutually_exclusive_group

    def recorded_add_argument(self: Any, *args: Any, **kwargs: Any) -> argparse.Action:
        action = add_argument(self, *args, **kwargs)
        owner = self if isinstance(self, argparse.ArgumentParser) else self._pitwall_owner
        names = tuple(action.option_strings) or (action.dest,)
        arguments.append((*_site(2), names, owner))
        return action

    def recorded_init(self: argparse.ArgumentParser, *args: Any, **kwargs: Any) -> None:
        parser_init(self, *args, **kwargs)
        events.append(["parser", *_site(2), None])

    def recorded_subparsers(self: argparse.ArgumentParser, *args: Any, **kwargs: Any) -> Any:
        action = add_subparsers(self, *args, **kwargs)
        events.append(["subparsers", *_site(2), None])
        return action

    def recorded_parser(self: Any, name: str, *args: Any, **kwargs: Any) -> Any:
        parser = add_parser(self, name, *args, **kwargs)
        events.append(["subcommand", *_site(2), name])
        return parser

    def owned(factory: Callable[..., Any]) -> Callable[..., Any]:
        def make(self: argparse.ArgumentParser, *args: Any, **kwargs: Any) -> Any:
            group = factory(self, *args, **kwargs)
            group._pitwall_owner = self
            return group

        return make

    argparse._ActionsContainer.add_argument = recorded_add_argument  # type: ignore[method-assign]  # reason: explorer deliberately monkeypatches argparse methods to record calls
    argparse.ArgumentParser.__init__ = recorded_init  # type: ignore[method-assign]  # reason: explorer deliberately monkeypatches argparse methods to record calls
    argparse.ArgumentParser.add_subparsers = recorded_subparsers  # type: ignore[method-assign]  # reason: explorer deliberately monkeypatches argparse methods to record calls
    argparse._SubParsersAction.add_parser = recorded_parser  # type: ignore[method-assign]  # reason: explorer deliberately monkeypatches argparse methods to record calls
    argparse.ArgumentParser.add_argument_group = owned(add_group)  # type: ignore[method-assign]  # reason: explorer deliberately monkeypatches argparse methods to record calls
    argparse.ArgumentParser.add_mutually_exclusive_group = owned(add_exclusive)  # type: ignore[method-assign]  # reason: explorer deliberately monkeypatches argparse methods to record calls


def _run(handler: Callable[[list[str]], Any], argv: list[str]) -> tuple[Any, str]:
    out = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    err = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code: Any = handler(argv)
        except SystemExit as exc:
            code = exc.code
        except Exception as exc:  # reason: record, never hide, a help path that crashes
            code = f"{type(exc).__name__}: {exc}"
    out.flush()
    return code, out.buffer.getvalue().decode()


def _explore(handler: Callable[[list[str]], Any], prefix: list[str], found: dict[str, Any]) -> None:
    path = " ".join(prefix)
    if path in found:
        return
    code, text = _run(handler, [*prefix, "--help"])
    found[path] = {"code": code, "help": text}
    usage = text.split("\n\n", 1)[0]
    for match in _SUBCOMMANDS.finditer(usage):
        for name in match.group(1).split(","):
            _explore(handler, [*prefix, name], found)


def main() -> None:
    _instrument()
    import pitwall.cli as pitwall_cli
    from pitwall.cli.runpod_market import cmd_runpod_catalogue

    pitwall: dict[str, Any] = {}
    _explore(pitwall_cli.main, [], pitwall)
    for group, _module, _function in pitwall_cli.GROUPS:
        _explore(pitwall_cli.main, [group], pitwall)
    # The registry serve parser is selected by `[personal] backend = "registry"` in pitwall.toml
    # (18-cli.md, `serve`); DATABASE_URL alone never selects it.
    with tempfile.TemporaryDirectory() as directory:
        config = Path(directory) / "pitwall.toml"
        config.write_text('[personal]\nbackend = "registry"\n')
        # A configured database alone must not select the registry backend; the config does.
        os.environ["DATABASE_URL"] = "postgresql://explorer:explorer@127.0.0.1:9/explorer"
        os.environ["PITWALL_CONFIG_FILE"] = str(config)
        registry: dict[str, Any] = {}
        _explore(pitwall_cli.main, ["serve"], registry)
        del os.environ["PITWALL_CONFIG_FILE"], os.environ["DATABASE_URL"]
    pitwall["serve (registry)"] = registry["serve"]
    # The market parser is built only after `runpod catalogue` dispatches past its help.
    _run(cmd_runpod_catalogue, ["--help"])

    helps: dict[int, str] = {}
    rows = []
    for file, line, names, parser in arguments:
        text = helps.setdefault(id(parser), parser.format_help())
        rows.append([file, line, list(names), text])
    document = {"helps": {"pitwall": pitwall}, "events": events, "arguments": rows}
    sys.stdout.write(json.dumps(document))


if __name__ == "__main__":
    main()
