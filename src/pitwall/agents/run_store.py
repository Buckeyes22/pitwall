"""Private, atomic local storage for dispatch artifacts."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import tempfile
import time
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .paths import config_root, ledger_path, state_root

if TYPE_CHECKING:
    from .mailbox import Mailbox

__all__ = ["config_root", "ledger_path", "state_root"]  # re-exported; paths.py resolves them


DIRECTORY_MODE = 0o700
FILE_MODE = 0o600


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


#: States after which a dispatch never changes again. ``paused`` is not terminal.
TERMINAL_STATES = frozenset({"preflight_failed", "succeeded", "failed", "timed_out", "cancelled"})


#: Set by the managed launcher for its supervisor: child output goes to the run logs only.
MANAGED_LAUNCH_ENV = "PITWALL_AGENTS_MANAGED_LAUNCH"


#: A run that recorded no supervisor pid is judged abandoned only after this long without a write.
PIDLESS_ABANDON_SECONDS = 24 * 3600.0


def newest_mtime(run_dir: Path) -> float:
    """The latest modification time of a run directory or any file directly in it."""
    newest = 0.0
    for path in (run_dir, *run_dir.iterdir()):
        try:
            newest = max(newest, path.stat().st_mtime)
        except OSError:
            continue
    return newest


def abandoned_reason(env: Mapping[str, str], run_dir: Path, now: float | None = None) -> str | None:
    """Why a non-terminal run has no live supervisor, or None while it may be alive.

    The supervisor recorded in ``run.json`` for the current attempt is authoritative for
    every run, managed or standalone: a managed launch sidecar neither exempts a dead
    supervisor (its harness would outlive it) nor condemns a live resumed one. A managed run
    that recorded no supervisor pid is still judged by its sidecar. Paused runs
    have no supervisor by design and are never abandoned here.
    """
    try:
        document = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    if not isinstance(document, dict):
        return None
    state = document.get("state")
    if not isinstance(state, str) or state in TERMINAL_STATES or state == "paused":
        return None
    supervisor = document.get("supervisor")
    pid = supervisor.get("pid") if isinstance(supervisor, dict) else None
    if isinstance(pid, int) and not isinstance(pid, bool):
        from .pids import pid_alive

        identity = supervisor.get("pidStartIdentity") if isinstance(supervisor, dict) else None
        if pid_alive(pid, identity if isinstance(identity, str) else None):
            return None
        return f"supervisor pid {pid} exited without recording a terminal state"
    if (state_root(env) / "launches" / run_dir.name).is_dir():
        return None  # no supervisor to judge: the managed launcher sidecar decides
    moment = time.time() if now is None else now
    if moment - newest_mtime(run_dir) < PIDLESS_ABANDON_SECONDS:
        return None
    return "no supervisor pid was recorded and the run has not changed for 24 hours"


def _prompt_retained(store: RunStore) -> bool | None:
    """Whether ``request.json`` records explicit prompt retention; ``None`` when unknown.

    Unknown covers a missing, unreadable, undecodable, corrupt, or wrongly shaped file.
    """
    try:
        request = json.loads(store.artifact("request.json").read_text(encoding="utf-8"))
    except OSError, ValueError:  # JSONDecodeError and UnicodeDecodeError are ValueErrors
        return None
    source = request.get("promptSource") if isinstance(request, dict) else None
    retained = source.get("retained") if isinstance(source, dict) else None
    return retained if isinstance(retained, bool) else None


#: How long a surviving harness gets between SIGTERM and SIGKILL when its run is abandoned.
ABANDONED_HARNESS_GRACE_SECONDS = 2.0


def terminate_harness(document: Mapping[str, Any], grace_seconds: float) -> int | None:
    """End the harness process group recorded in a run document; return its pgid if signalled.

    A supervisor killed outright (SIGKILL, OOM) cannot reap its harness, which runs in its
    own process group. The group is signalled only when its leader still carries the start
    identity recorded at launch (``pids.terminate_process_group``).
    """
    record = document.get("harnessProcess")
    if not isinstance(record, dict):
        return None
    pgid = record.get("pgid")
    identity = record.get("pidStartIdentity")
    if not isinstance(pgid, int) or isinstance(pgid, bool) or not isinstance(identity, str):
        return None
    from .pids import terminate_process_group

    return pgid if terminate_process_group(pgid, identity, grace_seconds) else None


def finalize_abandoned(env: Mapping[str, str], run_dir: Path, reason: str) -> None:
    """Record an abandoned run as failed, keeping the reason in ``abandoned.json``.

    A harness that outlived its supervisor is terminated first, so a ``failed`` record
    never sits in front of a harness that is still running.
    """
    store = RunStore(state_root(env), run_dir.name)
    document = json.loads(store.artifact("run.json").read_text(encoding="utf-8"))
    if document.get("state") in TERMINAL_STATES:
        return
    # The temp files written below bump the directory mtime; keep the run's real age so
    # cleanup and ``runs list`` do not make an old dead run look new.
    seen = run_dir.stat()
    terminated = terminate_harness(document, ABANDONED_HARNESS_GRACE_SECONDS) is not None
    stamp = utc_now()
    try:
        store.write_json(
            "abandoned.json",
            {
                "schemaVersion": 1,
                "reason": reason,
                "detectedAt": stamp,
                "harnessTerminated": terminated,
            },
        )
        transitions = document.get("transitions")
        document["state"] = "failed"
        document["transitions"] = [
            *(transitions if isinstance(transitions, list) else []),
            {"state": "failed", "timestamp": stamp},
        ]
        store.write_json("run.json", document)
        if _prompt_retained(store) is False:  # unknown keeps the prompt
            store.remove_delivery_prompt()
    finally:
        os.utime(run_dir, ns=(seen.st_atime_ns, seen.st_mtime_ns))


def reconcile_run(env: Mapping[str, str], run_dir: Path) -> str | None:
    """Record *run_dir* as failed when its supervisor is gone; return the reason, else None."""
    reason = abandoned_reason(env, run_dir)
    if reason is not None:
        finalize_abandoned(env, run_dir, reason)
    return reason


def ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=DIRECTORY_MODE)
    with contextlib.suppress(OSError):
        path.chmod(DIRECTORY_MODE)


def atomic_write_bytes(path: Path, content: bytes) -> None:
    ensure_private_directory(path.parent)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temporary)
    try:
        os.fchmod(descriptor, FILE_MODE)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        path.chmod(FILE_MODE)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temp_path.unlink()


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_bytes(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def atomic_create_bytes(path: Path, content: bytes) -> bool:
    """Create *path* holding *content*; return False if the name already exists.

    The content is written and fsynced under a temporary name first, then
    published with ``os.link``. ``link`` refuses an existing target, so two
    writers racing for one name can never overwrite each other the way
    ``os.replace`` does.
    """
    ensure_private_directory(path.parent)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temporary)
    try:
        os.fchmod(descriptor, FILE_MODE)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp_path, path)
        except FileExistsError:
            return False
        return True
    finally:
        with contextlib.suppress(FileNotFoundError):
            temp_path.unlink()


def atomic_create_json(path: Path, value: Any) -> bool:
    return atomic_create_bytes(
        path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    )


def append_jsonl(path: Path, value: Any) -> None:
    ensure_private_directory(path.parent)
    payload = (json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, FILE_MODE)
    try:
        os.write(descriptor, payload)
    finally:
        os.close(descriptor)
    with contextlib.suppress(OSError):
        path.chmod(FILE_MODE)


class RunStore:
    """Own one dispatch directory and its atomic JSON documents."""

    DELIVERY_NAME = "prompt.deliver.md"

    def __init__(self, root: Path, dispatch_id: str) -> None:
        self.state_root = root
        self.runs_root = root / "runs"
        self.dispatch_id = dispatch_id
        self.path = self.runs_root / dispatch_id

    @classmethod
    def create(cls, env: Mapping[str, str], dispatch_id: str) -> RunStore:
        store = cls(state_root(env), dispatch_id)
        ensure_private_directory(store.state_root)
        ensure_private_directory(store.runs_root)
        ensure_private_directory(store.path)
        return store

    def artifact(self, name: str) -> Path:
        return self.path / name

    def write_json(self, name: str, value: Any) -> None:
        atomic_write_json(self.artifact(name), value)

    def write_bytes(self, name: str, value: bytes) -> None:
        atomic_write_bytes(self.artifact(name), value)

    def touch_artifact(self, name: str) -> Path:
        path = self.artifact(name)
        ensure_private_directory(path.parent)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
        os.close(descriptor)
        return path

    def rotate_attempt_logs(self, previous_attempt: int) -> None:
        """Preserve the paused attempt's stdout/stderr before the resume truncates them."""
        for name in ("stdout.log", "stderr.log"):
            path = self.artifact(name)
            if path.exists():
                rotated = self.artifact(
                    f"{name.removesuffix('.log')}.attempt{previous_attempt}.log"
                )
                os.replace(path, rotated)

    def write_delivery_prompt(self, prompt: bytes) -> Path:
        self.write_bytes(self.DELIVERY_NAME, prompt)
        return self.artifact(self.DELIVERY_NAME)

    def remove_delivery_prompt(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            self.artifact(self.DELIVERY_NAME).unlink()

    def record_request(
        self,
        source: str,
        prompt: bytes | None,
        *,
        retain_prompt: bool,
        error: str | None = None,
        delivery: str = "inline",
    ) -> None:
        source_type = "stdin" if source == "-" else "file"
        request: dict[str, Any] = {
            "schemaVersion": 1,
            "dispatchId": self.dispatch_id,
            "promptSource": {
                "type": source_type,
                "path": None if source == "-" else source,
                "sha256": hashlib.sha256(prompt).hexdigest() if prompt is not None else None,
                "bytes": len(prompt) if prompt is not None else None,
                "retained": bool(retain_prompt and prompt is not None),
                "error": error,
                "delivery": delivery,
            },
        }
        self.write_json("request.json", request)
        if retain_prompt and prompt is not None:
            self.write_bytes("prompt.md", prompt)

    def artifact_summary(self) -> dict[str, str]:
        names = (
            "run.json",
            "request.json",
            "events.jsonl",
            "stdout.log",
            "stderr.log",
            "result.json",
            "workspace.json",
            "changeset.json",
            "changes.patch",
            "working.patch",
            "pause.json",
            "mailbox.json",
            "channel.json",
        )
        return {
            name.removesuffix(".json").removesuffix(".log").removesuffix(".patch"): str(
                self.artifact(name)
            )
            for name in names
            if self.artifact(name).exists()
        }

    def mailbox(self, *, max_asks: int | None = None) -> Mailbox:
        """Open this run's channel mailbox, creating ``mailbox/`` lazily.

        For write paths only; a read uses ``mailbox_if_present``. The returned
        mailbox keeps the ``mailbox.json`` summary artifact in sync on every
        write and on every read (reads may quarantine invalid files, which
        changes the counts). ``RunStore.create`` itself never creates ``mailbox/``.
        """
        return self._open_mailbox(max_asks, create=True)

    def mailbox_if_present(self, *, max_asks: int | None = None) -> Mailbox | None:
        """The run's mailbox, or ``None`` when ``mailbox/`` does not exist.

        Never creates ``mailbox/`` or ``mailbox.json``: a read of a channel-less
        run must not turn it into a channel run.
        """
        if not (self.path / "mailbox").is_dir():
            return None
        return self._open_mailbox(max_asks, create=False)

    def _open_mailbox(self, max_asks: int | None, *, create: bool) -> Mailbox:
        from .mailbox import DEFAULT_MAX_ASKS, Mailbox

        store = self

        if max_asks is None:
            # channel.py imports run_store; resolve the cap lazily to avoid a cycle.
            from .channel import load_channel_config

            config = load_channel_config(self.path)
            max_asks = config.max_asks if config is not None else None

        class _StoredMailbox(Mailbox):
            def __init__(self) -> None:
                super().__init__(
                    store.path,
                    store.dispatch_id,
                    max_asks=DEFAULT_MAX_ASKS if max_asks is None else max_asks,
                )
                self._refreshing = False

            def _refresh_summary(self) -> None:
                if self._refreshing:
                    return
                self._refreshing = True
                try:
                    # super().summary() re-enters self._read_box; the guard
                    # above makes that re-entry a no-op refresh.
                    summary = super().summary()
                    if summary != _read_summary(store.artifact("mailbox.json")):
                        store.write_json("mailbox.json", summary)
                finally:
                    self._refreshing = False

            def _write_doc(self, box: str, name: str, value: Any) -> dict[str, Any]:
                result = super()._write_doc(box, name, value)
                self._refresh_summary()
                return result

            def _read_box(self, box: str) -> list[dict[str, Any]]:
                # A plain read leaves the summary alone: the scheduler and MCP
                # poll pending_asks() every second. Only a read that quarantined
                # something changed the counts and needs a rewrite.
                before = self.quarantined
                docs = super()._read_box(box)
                if self.quarantined != before:
                    self._refresh_summary()
                return docs

        box = _StoredMailbox()
        if create:
            box.ensure()
            if not self.artifact("mailbox.json").exists():
                box._refresh_summary()
        return box

    def refresh_mailbox_summary(self, *, max_asks: int | None = None) -> dict[str, Any]:
        """Rewrite ``mailbox.json`` from the current mailbox state."""
        box = self.mailbox(max_asks=max_asks)
        summary = box.summary()
        self.write_json("mailbox.json", summary)
        return summary


def _read_summary(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return None


def list_runs(env: Mapping[str, str]) -> list[Path]:
    root = state_root(env) / "runs"
    if not root.is_dir():
        return []
    return sorted(
        (path for path in root.iterdir() if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def find_run(env: Mapping[str, str], dispatch_id: str) -> Path:
    matches = [
        path
        for path in list_runs(env)
        if path.name == dispatch_id or path.name.startswith(dispatch_id)
    ]
    if len(matches) != 1:
        qualifier = "not found" if not matches else "ambiguous"
        raise FileNotFoundError(f"run {dispatch_id!r} {qualifier}")
    return matches[0]


def _managed_run_is_live(env: Mapping[str, str], run_dir: Path) -> bool:
    """Keep a managed run while its supervisor, or failing that its launcher, is alive.

    Managed launch sidecars are intentionally separate from ``runs/`` so a
    parent MCP restart cannot lose the child. Cleanup must therefore inspect
    that sidecar before age-based removal. The supervisor recorded in ``run.json``
    for the current attempt is authoritative once the run is running: a resumed
    attempt has a new supervisor that the first attempt's sidecar does not name.
    The import is local because the managed launcher itself uses :mod:`run_store`
    for its durable records.
    """

    launch_dir = state_root(env) / "launches" / run_dir.name
    if not launch_dir.is_dir():
        return False
    try:
        run_doc = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError, TypeError:
        run_doc = {}
    if not isinstance(run_doc, dict):
        run_doc = {}
    from .managed_channel import _launcher_alive

    if run_doc.get("state") in TERMINAL_STATES:
        return False
    supervisor = run_doc.get("supervisor")
    pid = supervisor.get("pid") if isinstance(supervisor, dict) else None
    if run_doc.get("state") != "paused" and isinstance(pid, int) and not isinstance(pid, bool):
        from .pids import pid_alive

        identity = supervisor.get("pidStartIdentity") if isinstance(supervisor, dict) else None
        return pid_alive(pid, identity if isinstance(identity, str) else None)
    # The sidecar may be observed between directory creation and launcher.json
    # publication. Preserve it across this cleanup pass rather than racing the
    # child into an unrecoverable launch.
    if not (launch_dir / "launcher.json").is_file():
        return True
    try:
        return _launcher_alive(env, run_dir.name)
    except ImportError, OSError, TypeError, ValueError:
        # A malformed live sidecar remains inspectable for explicit cleanup;
        # an age pass must not destroy a run whose state is still nonterminal.
        return True


def _remove_managed_sidecar(env: Mapping[str, str], dispatch_id: str) -> None:
    launch_dir = state_root(env) / "launches" / dispatch_id
    if not launch_dir.exists() and not launch_dir.is_symlink():
        return
    if launch_dir.is_symlink():
        launch_dir.unlink()
    else:
        shutil.rmtree(launch_dir)


def _scrub_durable_managed_prompt(env: Mapping[str, str], dispatch_id: str) -> None:
    """Drop a staged managed prompt after the child owns its durable request.

    Cleanup can run in a freshly restarted MCP process, after the launch
    monitor from the previous process is gone.  The run's request record is
    the durable handoff boundary; use the sidecar request's retention bit
    before removing the duplicate staged copy.
    """

    run_dir = state_root(env) / "runs" / dispatch_id
    if not (run_dir / "request.json").is_file():
        return
    launch_dir = state_root(env) / "launches" / dispatch_id
    request = _read_summary(launch_dir / "request.json")
    if not isinstance(request, dict):
        return
    if request.get("retainPrompt") is True:
        return
    with contextlib.suppress(OSError):
        (launch_dir / "prompt.md").unlink()


def _cleanup_orphaned_launches(
    env: Mapping[str, str], *, now: float, older_than_seconds: float | None, remove_all: bool
) -> None:
    """Remove terminal launch sidecars whose run directory is already gone."""

    launches = state_root(env) / "launches"
    if not launches.is_dir():
        return
    for launch_dir in list(launches.iterdir()):
        if not launch_dir.is_dir() or launch_dir.is_symlink():
            continue
        try:
            uuid.UUID(launch_dir.name)
        except ValueError, AttributeError:
            continue
        if (state_root(env) / "runs" / launch_dir.name).exists():
            _scrub_durable_managed_prompt(env, launch_dir.name)
            continue
        launcher_record = launch_dir / "launcher.json"
        if launcher_record.is_file():
            try:
                from .managed_channel import _launcher_alive

                if _launcher_alive(env, launch_dir.name):
                    continue
            except ImportError, OSError, TypeError, ValueError:
                # Preserve an unreadable sidecar for an explicit operator
                # cleanup rather than guessing that its launcher is dead.
                continue
        elif not (
            (launch_dir / "launch_error.json").is_file() or (launch_dir / "orphan.json").is_file()
        ):
            # A launcher may be between sidecar creation and launcher.json
            # publication. Do not race that small window into deletion.
            continue
        try:
            age = now - launch_dir.stat().st_mtime
        except OSError:
            continue
        if not remove_all and (older_than_seconds is None or age < older_than_seconds):
            continue
        with contextlib.suppress(OSError):
            _remove_managed_sidecar(env, launch_dir.name)


def _standalone_run_may_be_alive(env: Mapping[str, str], run_dir: Path) -> bool:
    """Whether a standalone run is nonterminal and not paused, so its supervisor may be running.

    ``reconcile_run`` finalizes every such run it can prove abandoned, so one still nonterminal
    afterwards has a live supervisor or is too young to judge. Managed runs are judged by their
    launcher sidecar (``_managed_run_is_live``) and paused runs have no supervisor by design.
    An unreadable or stateless record is not a running run.
    """
    if (state_root(env) / "launches" / run_dir.name).is_dir():
        return False
    try:
        document = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    except OSError, ValueError:
        return False
    state = document.get("state") if isinstance(document, dict) else None
    return isinstance(state, str) and state not in TERMINAL_STATES and state != "paused"


def _run_state_is_active(run_dir: Path) -> bool:
    """Whether run.json names a nonterminal, unpaused state (or cannot be read at all)."""
    try:
        document = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    except OSError, ValueError:
        return False
    state = document.get("state") if isinstance(document, dict) else None
    return isinstance(state, str) and state not in TERMINAL_STATES and state != "paused"


def cleanup_runs(
    env: Mapping[str, str], *, older_than_seconds: float | None, remove_all: bool
) -> list[Path]:
    now = datetime.now(UTC).timestamp()
    removed: list[Path] = []
    for path in list_runs(env):
        reconciled = True
        try:
            reconcile_run(env, path)
        except OSError, ValueError:
            reconciled = False
        # This is intentionally before the age/live checks: a live child may
        # survive several MCP restarts, but its staged prompt is disposable as
        # soon as request.json exists in the run directory.
        _scrub_durable_managed_prompt(env, path.name)
        if remove_all or (
            older_than_seconds is not None and now - path.stat().st_mtime >= older_than_seconds
        ):
            if _managed_run_is_live(env, path) or _standalone_run_may_be_alive(env, path):
                continue
            if not reconciled and _run_state_is_active(path):
                continue  # its supervisor could not be judged; never delete a maybe-live run
            workspace_record = path / "workspace.json"
            if workspace_record.is_file():
                retained_path: Path | None = None
                try:
                    workspace = json.loads(workspace_record.read_text(encoding="utf-8"))
                    value = workspace.get("path")
                    if isinstance(value, str) and value:
                        retained_path = Path(value)
                except OSError, json.JSONDecodeError, TypeError:
                    pass
                if retained_path is not None and retained_path.is_dir():
                    continue
            from .channel import (
                distill_before_cleanup,  # channel imports run_store; import at call time
            )

            try:
                distill_before_cleanup(env, path)
            except OSError:
                continue  # keep the run until its routing signal can be recorded
            if _standalone_run_may_be_alive(env, path) or _managed_run_is_live(env, path):
                continue  # the run changed state while its signal was distilled
            shutil.rmtree(path)
            # The run itself has been removed. A stale sidecar is safe to
            # clean on a later explicit cleanup pass.
            with contextlib.suppress(OSError):
                _remove_managed_sidecar(env, path.name)
            removed.append(path)
    _cleanup_orphaned_launches(
        env,
        now=now,
        older_than_seconds=older_than_seconds,
        remove_all=remove_all,
    )
    return removed
