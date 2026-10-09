"""``pitwall setup`` writes the key export to the file the user's shell reads, quoted."""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest

from pitwall.personal import setup as personal_setup
from pitwall.personal.setup import run_setup


def _setup(home: Path, shell: str, state: Path) -> list[str]:
    lines: list[str] = []
    run_setup(
        environ={"HOME": str(home), "SHELL": shell, "RUNPOD_API_KEY": "k", "PATH": ""},
        home=home,
        state_root=state,
        prompt=lambda _q: True,
        run=lambda *a, **k: None,
        out=lines.append,
    )
    return lines


@pytest.mark.parametrize(
    ("platform", "shell", "profile"),
    [
        ("linux", "/bin/bash", ".bashrc"),
        ("darwin", "/bin/bash", ".bash_profile"),
        ("darwin", "/bin/zsh", ".zshrc"),
        ("linux", "/usr/bin/zsh", ".zshrc"),
        ("darwin", "", ".zshrc"),
        ("linux", "/usr/bin/fish", ".config/fish/config.fish"),
        ("darwin", "/opt/homebrew/bin/fish", ".config/fish/config.fish"),
    ],
)
def test_profile_follows_shell_and_platform(
    platform: str, shell: str, profile: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(personal_setup.sys, "platform", platform)
    home = tmp_path / "home"
    home.mkdir()

    _setup(home, shell, tmp_path / "state")

    assert "PITWALL_ENDPOINT_KEY" in (home / profile).read_text(encoding="utf-8")


def test_fish_gets_fish_syntax(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(personal_setup.sys, "platform", "linux")
    home = tmp_path / "home"
    home.mkdir()

    _setup(home, "/usr/bin/fish", tmp_path / "state")

    text = (home / ".config/fish/config.fish").read_text(encoding="utf-8")
    key_path = tmp_path / "state" / "endpoint.key"
    assert f"set -gx PITWALL_ENDPOINT_KEY (cat {shlex.quote(str(key_path))})" in text
    assert "export " not in text


def test_a_state_path_with_spaces_is_quoted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(personal_setup.sys, "platform", "linux")
    home = tmp_path / "home dir"
    home.mkdir()
    state = tmp_path / "state dir; $(touch pwned)"

    _setup(home, "/bin/bash", state)

    text = (home / ".bashrc").read_text(encoding="utf-8")
    key_path = state / "endpoint.key"
    assert f'export PITWALL_ENDPOINT_KEY="$(cat {shlex.quote(str(key_path))})"' in text
    assert shlex.split(f"cat {shlex.quote(str(key_path))}") == ["cat", str(key_path)]
