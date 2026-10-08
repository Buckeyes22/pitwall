"""The one place that resolves where Agent Routing keeps its state and config (stdlib only)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

STATE_HOME_ENV = "PITWALL_AGENTS_STATE_HOME"
LEDGER_ENV = "PITWALL_AGENTS_LEDGER"


def codex_home(env: Mapping[str, str], home: Path) -> Path:
    """``$CODEX_HOME`` (a leading ``~`` means ``home``), else ``<home>/.codex``."""
    configured = env.get("CODEX_HOME")
    if not configured:
        return home / ".codex"
    if configured == "~" or configured.startswith("~/"):
        return home / configured[2:]
    return Path(configured)


def state_root(env: Mapping[str, str]) -> Path:
    """``$XDG_STATE_HOME/pitwall/agents`` (default ``~/.local/state/pitwall/agents``).

    Holds the run store, mailbox, receipts, usage cache, and ledger.
    ``PITWALL_AGENTS_STATE_HOME`` relocates the whole root for isolated runs.
    """
    if env.get(STATE_HOME_ENV):
        return Path(env[STATE_HOME_ENV]).expanduser()
    base = Path(
        env.get("XDG_STATE_HOME", str(Path(env.get("HOME", "~")).expanduser() / ".local" / "state"))
    )
    return base / "pitwall" / "agents"


def config_root(env: Mapping[str, str]) -> Path:
    """``$XDG_CONFIG_HOME/pitwall/agents`` (default ``~/.config/pitwall/agents``).

    Holds ``hooks.json`` and ``harness-capabilities.json``.
    """
    base = Path(
        env.get("XDG_CONFIG_HOME", str(Path(env.get("HOME", "~")).expanduser() / ".config"))
    )
    return base / "pitwall" / "agents"


def ledger_path(env: Mapping[str, str]) -> Path:
    """The observations ledger the shims and distill aggregates append to."""
    configured = env.get(LEDGER_ENV)
    if configured:
        return Path(configured).expanduser()
    return state_root(env) / "ledger" / "observations.jsonl"
