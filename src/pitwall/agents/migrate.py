"""``pitwall agents migrate``: the clean break for a workstation that ran standalone Agent Routing.

The steps run in this order, and the command is idempotent (a second run finds nothing to do):

1. ``routes.json`` (and its later ``profiles.json`` name) becomes the ``[agents.profiles]`` tables of ``pitwall.toml``.
   A differing value already in the file is a conflict; every conflict is listed and nothing changes.
2. The run store, mailbox, receipts, usage cache, ledger, and the other config files move to
   the new state and config roots.
3. The installed shims and plugin registrations are rewritten to the ``pitwall agents`` commands,
   and the ``managed by subagent-model-routing`` blocks the dsh, hermes, and opencode adapters
   wrote into the harnesses' own config files are re-marked ``managed by pitwall``.
4. Dispatch worktree branches under every earlier prefix (``LEGACY_BRANCH_PREFIXES``) are renamed
   ``pitwall-agents/<id>`` in the repository that owns them, and the run's ``workspace.json``
   (including the moved worktree's path) is updated so the workspace ownership check accepts it.
   When the branch or the repository is gone, the record's path is still pointed at the moved
   worktree if that directory exists; otherwise the record is reported as stale. A target branch
   that already exists is a conflict, reported with that repository left unchanged.
5. Environment variables still set under old names are reported with their replacement.
6. The old install is removed, only after steps 1 to 3 succeeded, and each removed path is printed.

Exit 1 is also returned, after everything else ran, when a worktree branch conflicts.
It refuses (exit 2) while the legacy run store holds an active dispatch. Exit 1 is a conflict or an
unreadable input, with nothing changed.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import installation, mcp_registration, profiles, profiles_toml
from .harnesses.base import parse_duration_seconds
from .harnesses.dsh import DshAdapter
from .harnesses.hermes import HermesAdapter
from .harnesses.opencode import OpenCodeAdapter
from .installation import InstallationError, Runner
from .managed_channel import DEFAULT_DISPATCH_SECONDS
from .migrate_env import (
    LEGACY_BRANCH_PREFIXES,
    LEGACY_PATHS,
    LEGACY_PREFIXES,
    legacy_env_conflicts,
)
from .paths import config_root, ledger_path, state_root
from .pids import pid_alive
from .registry import load_registry
from .run_store import TERMINAL_STATES, atomic_write_bytes
from .run_store import newest_mtime as _newest_mtime
from .workspace import worktree_path

EXIT_CONFLICT = 1
EXIT_ACTIVE_RUNS = 2
_NEW_BRANCH_PREFIX = "pitwall-agents/"

_LEGACY_CONFIG, _LEGACY_STATE, _LEGACY_LEDGER_HOME, _LEGACY_SHARE = LEGACY_PATHS
_LEGACY_STATE_HOME_ENV = LEGACY_PREFIXES[0] + "STATE_HOME"
_PROFILE_FILES = ("routes.json", "profiles.json")
_LEDGER_DIR = "ledger"

# The 16 files the standalone installer put in ~/.claude/scripts, and the text that marks each as
# the standalone version rather than a file the user or the new installer wrote.
_LAUNCHER = "pitwall-agent-routing"
# The launcher's older name; the old installer left it as a symlink to the launcher.
_LEGACY_ALIAS = "model-routing"
_PARSER = "parse-shim-result.py"
_LEGACY_SHIMS = (
    "codex-shim.sh",
    "opencode-shim.sh",
    "grok-shim.sh",
    "goose-shim.sh",
    "claude-shim.sh",
    "cline-shim.sh",
    "dsh-shim.sh",
    "kimi-shim.sh",
    "muse-shim.sh",
    "pi-shim.sh",
    "qwen-shim.sh",
    "hermes-shim.sh",
    "agy-shim.sh",
    "route-shim.sh",
)
_SHIM_MARKER = _LAUNCHER
_PARSER_MARKER = "SHIM-RESULT"

# Plugin registrations the standalone installer made, removed by their qualified names so the
# plugins the new installer registers are left alone.
_LEGACY_MARKETPLACE = "subagent-model-routing-local"
_LEGACY_CLAUDE_MARKETPLACE = "pitwall"
_LEGACY_PLUGIN_COMMANDS: dict[str, tuple[tuple[str, ...], ...]] = {
    "claude": (
        (
            "claude",
            "plugin",
            "uninstall",
            "--keep-data",
            "--scope",
            "user",
            f"pitwall@{_LEGACY_CLAUDE_MARKETPLACE}",
        ),
        # No --scope: the old install declared the marketplace in known_marketplaces.json, where
        # `--scope user` fails while still exiting 0. Without a scope it is removed from all.
        ("claude", "plugin", "marketplace", "remove", _LEGACY_CLAUDE_MARKETPLACE),
    ),
    "codex": (
        ("codex", "plugin", "remove", f"pitwall-codex@{_LEGACY_MARKETPLACE}"),
        ("codex", "plugin", "marketplace", "remove", _LEGACY_MARKETPLACE),
    ),
    # The plugin id is the same as the new install's, so only the old marketplace is removed.
    "copilot": (("copilot", "plugin", "marketplace", "remove", _LEGACY_MARKETPLACE),),
}


# The marker the harness adapters wrote into the user's harness config files before the merge.
# dsh and hermes put it in a YAML comment line; opencode prefixes the provider ``name``.
_LEGACY_MARKER = "# managed by subagent-model-routing"
_MARKER = "# managed by pitwall"
_LEGACY_OPENCODE_NAME = re.compile(r'("name"\s*:\s*")subagent-model-routing: ')


# The launcher names the old install registered the pitwall-channel server under, and the block
# marker it wrote into Codex's config.toml. The old server ran as ``<launcher> mcp``.
_LEGACY_CHANNEL_COMMANDS = frozenset({_LAUNCHER, _LEGACY_ALIAS})
_LEGACY_CODEX_BEGIN = "# >>> pitwall-channel (managed by pitwall-agent-routing setup mcp)"


def _is_legacy_channel_entry(entry: Any) -> bool:
    argv = mcp_registration.entry_argv(entry)
    if argv is None:
        return False
    command, args = argv
    return Path(command).name in _LEGACY_CHANNEL_COMMANDS and args[:1] == ["mcp"]


_LEGACY_CHANNEL = mcp_registration.ChannelAdoption(_is_legacy_channel_entry, _LEGACY_CODEX_BEGIN)


def _remark(path: Path, rewrite: Callable[[str], str]) -> bool:
    """Rewrite one harness config file in place; True when the old marker was found."""
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError, UnicodeDecodeError:
        return False
    updated = rewrite(text)
    if updated == text:
        return False
    atomic_write_bytes(path, updated.encode("utf-8"))
    return True


def _rewrite_yaml_marker(text: str) -> str:
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.strip() == _LEGACY_MARKER:
            lines[index] = line.replace(_LEGACY_MARKER, _MARKER)
    return "".join(lines)


def _rewrite_opencode_marker(text: str) -> str:
    return _LEGACY_OPENCODE_NAME.sub(r"\1pitwall: ", text)


def _remark_harness_configs(env: Mapping[str, str], home: Path) -> list[Path]:
    targets: list[tuple[Path, Callable[[str], str]]] = [
        (DshAdapter.dsh_home(env, home) / "settings.yaml", _rewrite_yaml_marker),
        (HermesAdapter.hermes_home(env, home) / "config.yaml", _rewrite_yaml_marker),
        (OpenCodeAdapter.config_path(env, home), _rewrite_opencode_marker),
    ]
    return [path for path, rewrite in targets if _remark(path, rewrite)]


@dataclass(slots=True)
class _Moves:
    """File moves from a legacy tree into the new tree, planned before anything changes."""

    moves: list[tuple[Path, Path]] = field(default_factory=list)
    duplicates: list[Path] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    tops: list[tuple[Path, Path]] = field(default_factory=list)

    def plan(self, source: Path, target: Path) -> None:
        """Plan one top-level item; its moves are reported together."""
        self.tops.append((source, target))
        _plan_move(source, target, self)


@dataclass(slots=True)
class _Legacy:
    home: Path
    config: Path
    state: Path
    ledger_home: Path
    share: Path
    scripts: Path
    bin_launcher: Path
    profile_files: list[Path]
    shims: list[Path]
    leftovers: list[Path]


def _legacy_paths(env: Mapping[str, str], home: Path) -> _Legacy:
    def under(literal: str) -> Path:
        return home / literal.removeprefix("~/")

    state = (
        Path(env[_LEGACY_STATE_HOME_ENV]).expanduser()
        if env.get(_LEGACY_STATE_HOME_ENV)
        else under(_LEGACY_STATE)
    )
    config = under(_LEGACY_CONFIG)
    scripts = installation.InstallLocations.for_home(home, env).scripts
    shims: list[Path] = []
    leftovers: list[Path] = []
    for name in _LEGACY_SHIMS:
        if _has_marker(scripts / name, _SHIM_MARKER):
            (shims if name in installation.shim_names() else leftovers).append(scripts / name)
    if _has_marker(scripts / _PARSER, _PARSER_MARKER):
        leftovers.append(scripts / _PARSER)
    for launcher in (scripts / _LAUNCHER, home / ".local" / "bin" / _LAUNCHER):
        if launcher.is_symlink() or launcher.is_file():
            leftovers.append(launcher)
    for alias in (scripts / _LEGACY_ALIAS, home / ".local" / "bin" / _LEGACY_ALIAS):
        if _is_legacy_alias(alias):
            leftovers.append(alias)
    return _Legacy(
        home=home,
        config=config,
        state=state,
        ledger_home=under(_LEGACY_LEDGER_HOME),
        share=under(_LEGACY_SHARE),
        scripts=scripts,
        bin_launcher=home / ".local" / "bin" / _LAUNCHER,
        profile_files=[config / name for name in _PROFILE_FILES if (config / name).is_file()],
        shims=shims,
        leftovers=leftovers,
    )


def _is_legacy_alias(path: Path) -> bool:
    """True for a symlink to the old launcher (even dangling) or a file with the old marker."""
    if path.is_symlink():
        try:
            return Path(os.readlink(path)).name in _LEGACY_CHANNEL_COMMANDS
        except OSError:
            return False
    return _has_marker(path, _SHIM_MARKER)


def _has_marker(path: Path, marker: str) -> bool:
    if not path.is_file():
        return False
    try:
        return marker in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def _install_present(legacy: _Legacy) -> bool:
    return bool(legacy.shims or legacy.leftovers or legacy.share.exists())


# Slack added to a run's dispatch timeout before its files count as stale.
_ACTIVITY_GRACE_SECONDS = 300.0


def _read_object(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {}
    return document if isinstance(document, dict) else {}


def _run_timeout(legacy: _Legacy, run: Path, env: Mapping[str, str]) -> float:
    """The dispatch timeout the run itself recorded, else the configured one, else the default."""
    for source in (legacy.state / "launches" / run.name / "request.json", run / "request.json"):
        recorded = _read_object(source).get("timeoutSeconds")
        if isinstance(recorded, int | float) and not isinstance(recorded, bool) and recorded > 0:
            return float(recorded)
    try:
        return parse_duration_seconds(env.get("PITWALL_AGENTS_TIMEOUT_SECS", ""))
    except ValueError:
        return DEFAULT_DISPATCH_SECONDS


def _classify_runs(
    legacy: _Legacy, env: Mapping[str, str], now: float
) -> tuple[list[str], list[str]]:
    """Split the non-terminal runs into ``(active, abandoned)`` report lines.

    A run is active when its launcher pid is still alive (same start identity), or, when it
    recorded no pid, when a file in its directory changed within its timeout plus a grace period.
    Every other non-terminal run belongs to a dispatch whose runner died.
    """
    runs = legacy.state / "runs"
    if not runs.is_dir():
        return [], []
    active: list[str] = []
    abandoned: list[str] = []
    for run in sorted(runs.iterdir()):
        state = _read_object(run / "run.json").get("state")
        if not isinstance(state, str) or state in TERMINAL_STATES:
            continue
        launcher = _read_object(run / "launcher.json") or _read_object(
            legacy.state / "launches" / run.name / "launcher.json"
        )
        pid = launcher.get("pid")
        last = _newest_mtime(run)
        if isinstance(pid, int) and not isinstance(pid, bool):
            identity = launcher.get("pidStartIdentity")
            alive = pid_alive(pid, identity if isinstance(identity, str) else None)
        else:
            window = _run_timeout(legacy, run, env) + _ACTIVITY_GRACE_SECONDS
            alive = now - last <= window
        if alive:
            active.append(f"{run.name} ({state})")
        else:
            stamp = datetime.fromtimestamp(last, UTC).isoformat(timespec="seconds")
            abandoned.append(f"{run.name} ({state}, last activity {stamp})")
    return active, abandoned


def _prune(value: Any) -> Any:
    """Drop what TOML cannot store or ``profiles_toml`` would not write: nulls and empties."""
    if isinstance(value, dict):
        pruned = {key: _prune(child) for key, child in value.items() if child is not None}
        return {key: child for key, child in pruned.items() if child not in ({}, [])}
    return value


def _merge(
    existing: Mapping[str, Any], incoming: Mapping[str, Any], path: str, conflicts: list[str]
) -> dict[str, Any]:
    merged = dict(existing)
    for key, value in incoming.items():
        where = f"{path}.{key}"
        if key not in existing:
            merged[key] = value
        elif isinstance(existing[key], dict) and isinstance(value, dict):
            merged[key] = _merge(existing[key], value, where, conflicts)
        elif existing[key] != value:
            conflicts.append(
                f"{where}: pitwall.toml has {existing[key]!r}, the old config has {value!r}"
            )
    return merged


def _contains(whole: Mapping[str, Any], part: Mapping[str, Any]) -> bool:
    return all(
        key in whole
        and (
            _contains(whole[key], value)
            if isinstance(value, dict) and isinstance(whole[key], dict)
            else whole[key] == value
        )
        for key, value in part.items()
    )


def _plan_move(source: Path, target: Path, plan: _Moves) -> None:
    if source.is_dir() and not source.is_symlink():
        if target.exists() and not target.is_dir():
            plan.conflicts.append(f"{target}: exists and is not a directory (old: {source})")
            return
        for child in sorted(source.iterdir()):
            _plan_move(child, target / child.name, plan)
    elif not target.exists() and not target.is_symlink():
        plan.moves.append((source, target))
    elif (
        target.is_file()
        and source.is_file()
        and not source.is_symlink()
        and target.read_bytes() == source.read_bytes()
    ):
        plan.duplicates.append(source)
    else:
        plan.conflicts.append(f"{target}: already exists and differs from {source}")


def _created_dir_times(plan: _Moves) -> dict[Path, tuple[int, int]]:
    """Destination directories the moves will create, with their source directories' times."""
    times: dict[Path, tuple[int, int]] = {}
    for source, destination in plan.moves:
        top = next(t for t, _ in plan.tops if t == source or t in source.parents)
        old, new = source.parent, destination.parent
        while old != top.parent:
            if new not in times and not new.exists():
                stat = old.stat()
                times[new] = (stat.st_atime_ns, stat.st_mtime_ns)
            old, new = old.parent, new.parent
    return times


def _prune_empty(root: Path) -> None:
    if not root.is_dir() or root.is_symlink():
        return
    for directory in sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            continue  # reason: not empty; only empty directories are removed
    try:
        root.rmdir()
    except OSError:
        return  # reason: something the migration does not own is still inside


def _remove(path: Path, out: Callable[[str], None]) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)
    out(f"  removed {path}")


def migrate(
    env: Mapping[str, str],
    home: Path,
    *,
    runner: Runner | None = None,
    harnesses: Sequence[str] | None = None,
    plugin_hosts: Sequence[str] | None = None,
    out: Callable[[str], None] = print,
    err: Callable[[str], None] = lambda text: print(text, file=sys.stderr),
) -> int:
    """Run the migration for *home*; return the process exit status.

    *harnesses* and *plugin_hosts* select the install's registration targets exactly as
    ``installation.install`` does (default: whatever is detected).
    """
    legacy = _legacy_paths(env, home)
    target_config = config_root(env)
    target_state = state_root(env)
    target_toml = profiles.profiles_path(env)

    active, abandoned = _classify_runs(legacy, env, time.time())
    if active:
        err("pitwall agents migrate: refusing to run; these dispatches are still active:")
        for item in active:
            err(f"  {item}")
        err("Wait for them to finish (or cancel them), then run the migration again.")
        return EXIT_ACTIVE_RUNS
    if abandoned:
        out(
            f"{len(abandoned)} non-terminal dispatch record(s) have no live runner and are "
            "treated as abandoned; their state is moved over unchanged."
        )
        for item in abandoned:
            out(f"treated as abandoned: {item}")

    conflicts: list[str] = []

    # Step 1, planned: the profiles tables.
    incoming: dict[str, Any] = {}
    for source in legacy.profile_files:
        try:
            document = json.loads(source.read_text(encoding="utf-8"))
            profiles.validate_profiles(document, registry=load_registry())
        except (
            OSError,
            ValueError,
        ) as exc:  # reason: JSON, encoding, and ProfilesError are ValueErrors
            err(f"pitwall agents migrate: cannot use {source}: {exc}")
            return EXIT_CONFLICT
        body = _prune({key: value for key, value in document.items() if key != "schemaVersion"})
        incoming = _merge(incoming, body, f"{source.name}", conflicts)
    existing_text = target_toml.read_text(encoding="utf-8") if target_toml.is_file() else ""
    try:
        existing = profiles_toml.parse(existing_text)
    except (ValueError, tomllib.TOMLDecodeError) as exc:
        err(f"pitwall agents migrate: cannot read {target_toml}: {exc}")
        return EXIT_CONFLICT
    merged = _merge(existing, incoming, "agents.profiles", conflicts)

    # Step 2, planned: state and the other config files.
    moves = _Moves()
    if legacy.state.is_dir():
        for child in sorted(legacy.state.iterdir()):
            moves.plan(child, target_state / child.name)
    ledger_source = legacy.ledger_home / _LEDGER_DIR
    if ledger_source.is_dir():
        moves.plan(ledger_source, ledger_path(env).parent)
    if legacy.config.is_dir():
        for child in sorted(legacy.config.iterdir()):
            if child not in legacy.profile_files:
                moves.plan(child, target_config / child.name)
    conflicts.extend(moves.conflicts)

    if conflicts:
        err("pitwall agents migrate: conflicts found; nothing was changed. Resolve each one:")
        for conflict in conflicts:
            err(f"  {conflict}")
        return EXIT_CONFLICT

    installing = _install_present(legacy)
    changed = bool(incoming and merged != existing) or bool(moves.moves) or installing
    changed = changed or bool(moves.duplicates) or bool(legacy.profile_files)

    # Step 1, applied.
    if incoming and merged != existing:
        rewritten = profiles_toml.replace_profiles(existing_text, merged)
        target_toml.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(target_toml, rewritten.encode("utf-8"))
        out(f"wrote [agents.profiles] to {target_toml}")
    if legacy.profile_files and not _contains(profiles_toml.parse(_read(target_toml)), incoming):
        err(f"pitwall agents migrate: {target_toml} does not hold the old profiles; stopping")
        return EXIT_CONFLICT

    # Step 2, applied. Moves are per file, so each destination directory is new; it takes its
    # source directory's times, which `runs cleanup --older-than` uses to age a run.
    dir_times = _created_dir_times(moves)
    for source, destination in moves.moves:
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.move(source, destination)
    for directory, times in dir_times.items():
        with contextlib.suppress(OSError):
            os.utime(directory, ns=times)
    for top, target in moves.tops:
        count = sum(1 for source, _ in moves.moves if source == top or top in source.parents)
        if count == 1 and not any(top in source.parents for source, _ in moves.moves):
            out(f"moved {top} -> {target}")
        elif count:
            out(f"moved {top} -> {target} ({count:,} entries)")

    # Step 3, first the harnesses' own config files.
    remarked = _remark_harness_configs(env, home)
    for path in remarked:
        out(f"rewrote managed marker in {path}")
    changed = changed or bool(remarked)

    # Step 3.
    if installing:
        code = _reinstall(env, home, legacy, runner, harnesses, plugin_hosts, out, err)
        if code:
            return code

    # Step 4.
    worktrees_changed, worktree_conflicts = _migrate_worktree_branches(env, target_state, out, err)
    changed = changed or worktrees_changed

    # Step 5.
    stale = legacy_env_conflicts(env)
    if stale:
        out("Environment variables still set under old names (rename each in your shell profile):")
        for name, replacement in stale.items():
            out(f"  {name} -> {replacement}")

    # Step 6.
    _remove_old_install(env, legacy, moves, runner, out, err)
    if not changed and not stale:
        out("nothing to migrate")
    return EXIT_CONFLICT if worktree_conflicts else 0


def _git_in(common_dir: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *argv],
        cwd=common_dir,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C.UTF-8"},
        capture_output=True,
        text=True,
        check=False,
    )


def _has_branch(common_dir: Path, branch: str) -> bool:
    ref = f"refs/heads/{branch}"
    return _git_in(common_dir, "show-ref", "--verify", "--quiet", ref).returncode == 0


def _follow_moved_worktree(
    env: Mapping[str, str],
    dispatch_id: str,
    record: dict[str, Any],
    record_path: Path,
    reason: str,
    out: Callable[[str], None],
) -> bool:
    """Point a record whose branch cannot be renamed at the moved worktree; True if rewritten.

    The moved worktree may still hold unapplied work, so the record has to name it for
    `runs discard` and cleanup to find it. There is no branch to rename, so the record also
    takes the new branch name that `runs discard` checks ownership against. With no worktree
    there, the record is reported stale.
    """
    moved = worktree_path(env, dispatch_id)
    if not moved.is_dir():
        out(f"stale record {dispatch_id}: {reason}; no worktree at {moved}, record unchanged")
        return False
    record["path"] = str(moved)
    record["branch"] = _NEW_BRANCH_PREFIX + dispatch_id
    atomic_write_bytes(record_path, json.dumps(record, indent=2).encode("utf-8"))
    out(f"updated {dispatch_id}: {reason}; record now names {moved}")
    return True


def _migrate_worktree_branches(
    env: Mapping[str, str],
    state: Path,
    out: Callable[[str], None],
    err: Callable[[str], None],
) -> tuple[bool, bool]:
    """Rename each recorded dispatch branch to the new prefix; return (changed, conflict)."""
    changed = conflict = False
    for record_path in sorted((state / "runs").glob("*/workspace.json")):
        dispatch_id = record_path.parent.name
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except OSError, ValueError:
            continue
        if not isinstance(record, dict):
            continue
        prefix = next(
            (p for p in LEGACY_BRANCH_PREFIXES if record.get("branch") == p + dispatch_id), None
        )
        if prefix is None:
            continue
        old = prefix + dispatch_id
        new = _NEW_BRANCH_PREFIX + dispatch_id
        common_dir = Path(str(record.get("repositoryCommonDir", "")))
        if not common_dir.is_dir():
            reason = f"repository {common_dir} no longer exists"
            changed = (
                _follow_moved_worktree(env, dispatch_id, record, record_path, reason, out)
                or changed
            )
            continue
        try:
            has_old, has_new = _has_branch(common_dir, old), _has_branch(common_dir, new)
        except FileNotFoundError:
            err(
                "pitwall agents migrate: git not found on PATH; the dispatch worktree branches "
                "were not renamed. Install git and run `pitwall agents migrate` again."
            )
            return changed, True
        if has_old and has_new:
            err(
                f"pitwall agents migrate: {dispatch_id}: branch {new} already exists in "
                f"{common_dir}; not renaming {old} (resolve it by hand)"
            )
            conflict = True
            continue
        if has_old:
            renamed = _git_in(common_dir, "branch", "-m", old, new)
            if renamed.returncode != 0:
                err(
                    f"pitwall agents migrate: {dispatch_id}: cannot rename {old}: "
                    f"{renamed.stderr.strip()}"
                )
                conflict = True
                continue
            out(f"renamed branch {old} -> {new} in {common_dir}")
        elif not has_new:
            reason = f"branch {old} no longer exists in {common_dir}"
            changed = (
                _follow_moved_worktree(env, dispatch_id, record, record_path, reason, out)
                or changed
            )
            continue
        record["branch"] = new
        moved = worktree_path(env, dispatch_id)
        if record.get("path") != str(moved) and moved.is_dir():
            record["path"] = str(moved)
            _git_in(common_dir, "worktree", "repair", str(moved))
        atomic_write_bytes(record_path, json.dumps(record, indent=2).encode("utf-8"))
        changed = True
    return changed, conflict


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _reinstall(
    env: Mapping[str, str],
    home: Path,
    legacy: _Legacy,
    runner: Runner | None,
    harnesses: Sequence[str] | None,
    plugin_hosts: Sequence[str] | None,
    out: Callable[[str], None],
    err: Callable[[str], None],
) -> int:
    # install refuses to overwrite a file it did not write, so the standalone shims that share a
    # name with a new one are lifted out first and put back if the install fails.
    lifted = {path: path.read_bytes() for path in legacy.shims}
    for path in lifted:
        path.unlink()
    try:
        with mcp_registration.adopting(_LEGACY_CHANNEL):
            result = installation.install(
                env, home, runner=runner, harnesses=harnesses, plugin_hosts=plugin_hosts
            )
    except InstallationError as exc:
        for path, content in lifted.items():
            path.write_bytes(content)
            path.chmod(0o755)
        err(f"pitwall agents migrate: the shim and plugin install failed: {exc}")
        err("The old install was left in place. Fix the problem and run the migration again.")
        return EXIT_CONFLICT
    for path in result.files:
        out(f"installed {path}")
    for warning in result.warnings:
        err(f"warning: {warning}")
    return 0


def _remove_old_install(
    env: Mapping[str, str],
    legacy: _Legacy,
    moves: _Moves,
    runner: Runner | None,
    out: Callable[[str], None],
    err: Callable[[str], None],
) -> None:
    present = _install_present(legacy)
    doomed = [
        *legacy.leftovers,
        *(p for p in (legacy.share,) if p.exists()),
        *legacy.profile_files,
        *moves.duplicates,
    ]
    if not (present or doomed):
        return
    out("Removing the old install:")
    if present:
        run = runner or installation.run_command(env)
        search_path = env.get("PATH")
        for host, commands in _LEGACY_PLUGIN_COMMANDS.items():
            if shutil.which(host, path=search_path) is None:
                continue
            for argv in commands:
                if run(argv) != 0:
                    err(f"warning: {host}: could not run {' '.join(argv)}")
                else:
                    out(f"  ran {' '.join(argv)}")
    for path in doomed:
        _remove(path, out)
    for root in (legacy.state, legacy.ledger_home, legacy.config):
        before = root.exists()
        _prune_empty(root)
        if before and not root.exists():
            out(f"  removed {root}")
        elif root.exists():
            err(f"note: {root} still holds files the migration does not own; left in place")


def main(argv: Sequence[str]) -> int:
    """Entry point for ``pitwall agents migrate``."""
    if list(argv):
        print("usage: pitwall agents migrate", file=sys.stderr)
        return 2
    env = dict(os.environ)
    return migrate(env, installation.default_home(env))
