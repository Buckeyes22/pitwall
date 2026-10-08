"""Every fenced `pitwall ...` command in qa/ names a real command group and subcommand."""

from __future__ import annotations

import functools
import re
import subprocess
import sys
from pathlib import Path

from tests.hang_guard import HANG_GUARD_SECS

ROOT = Path(__file__).resolve().parents[2]
QA = ROOT / "qa"
FENCE = re.compile(r"^\s*```")
COMMAND = re.compile(r"^\s*(?:\$\s+)?(?:uv run\s+)?pitwall(?:\s+(?P<rest>.*))?$")
CHOICES = re.compile(r"\{([^{}]+)\}(?:\s+\.\.\.|\s*$)|\{([^{}|]*\|[^{}]*)\}")
WORD = re.compile(r"^[a-z][a-z0-9_-]*$")


def _fenced_commands() -> list[tuple[str, list[str]]]:
    found: list[tuple[str, list[str]]] = []
    for page in sorted(QA.rglob("*.md")):
        in_fence = False
        for number, line in enumerate(page.read_text(encoding="utf-8").splitlines(), start=1):
            if FENCE.match(line):
                in_fence = not in_fence
                continue
            match = COMMAND.match(line) if in_fence else None
            if match is None or not match["rest"]:
                continue
            rest = re.split(r"\s(?:\||;|&&|>|2>|<)|\s#", " " + match["rest"], maxsplit=1)[0]
            words = rest.split()
            if words:
                found.append((f"{page.relative_to(ROOT)}:{number}", words))
    return found


@functools.cache
def _help(path: tuple[str, ...]) -> tuple[int, str]:
    done = subprocess.run(
        [sys.executable, "-m", "pitwall", *path, "--help"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=HANG_GUARD_SECS,
        check=False,
    )
    return done.returncode, done.stdout + done.stderr


def _choices(text: str) -> set[str] | None:
    """Return the subcommand names a help text offers, or None for a leaf command."""
    flat = " ".join(text.split("positional arguments:")[0].split())
    hit = CHOICES.search(flat)
    if hit is None:
        return None
    body = hit.group(1) or hit.group(2)
    return {re.split(r"[\s\[]", part.strip())[0] for part in re.split(r"[|,]", body)}


def test_every_qa_command_is_a_real_command() -> None:
    commands = _fenced_commands()
    assert commands, "no fenced pitwall commands found in qa/"
    problems: list[str] = []
    for where, words in commands:
        path: list[str] = []
        top = _help(())[1]
        for word in words:
            if not WORD.match(word):
                break
            choices = _choices(_help(tuple(path))[1] if path else top)
            if choices is None:
                break
            if not path:
                choices = choices | set(re.findall(r"^\s+pitwall ([a-z][a-z0-9-]*)", top, re.M))
            if word not in choices:
                problems.append(f"{where}: `pitwall {' '.join([*path, word])}` is not a command")
                break
            path.append(word)
            code, text = _help(tuple(path))
            if code != 0 or "Unknown command" in text:
                problems.append(f"{where}: `pitwall {' '.join(path)} --help` failed")
                break
    assert not problems, "\n".join(problems)
