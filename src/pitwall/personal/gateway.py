"""Child-process supervision for the loopback gateway in personal mode (Decision Q2)."""

from __future__ import annotations

import datetime as dt
import logging
import os
import signal
import socket
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

import httpx
from pydantic import BaseModel

from pitwall.personal.state import StateStore

log = logging.getLogger("pitwall.personal.gateway")

GATEWAY_STATE = "gateway.json"
STOP_WAIT_S = 5.0
"""Seconds stop() waits for the gateway to exit after SIGTERM, and again after SIGKILL."""
DEFAULT_HEALTH_URL = "http://127.0.0.1:20130/health"
DEFAULT_PORT = 20130
DEFAULT_BIND = "127.0.0.1"
PROC_ROOT = Path("/proc")


_ROUTES_IN_CHECKOUT = Path(__file__).resolve().parents[3] / "config" / "gateway-routes.json"
_ROUTES_PACKAGED = (
    Path(__file__).resolve().parents[1]
    / "gateway_catalog"
    / "data"
    / "config"
    / "gateway-routes.json"
)


def routes_path() -> Path:
    """The provider route table: the checkout copy when present, else the wheel's copy."""
    return _ROUTES_IN_CHECKOUT if _ROUTES_IN_CHECKOUT.is_file() else _ROUTES_PACKAGED


def default_program() -> list[str]:
    """The gateway command: ``pitwall gateway serve`` under the interpreter running this code.

    ``sys.executable`` is absolute, so the launch never depends on the cwd or on PATH.
    """
    return [sys.executable, "-m", "pitwall", "gateway", "serve"]


def default_argv() -> list[str]:
    return [*default_program(), "--port", str(DEFAULT_PORT), "--bind", DEFAULT_BIND]


DEFAULT_ARGV = default_argv()


class GatewayPortInUse(RuntimeError):
    """The gateway port is already bound by something else; the message names it."""


def port_holder(bind: str, port: int, *, proc: Path = Path("/proc")) -> str | None:
    """Describe what holds ``bind:port``, or None when the port is free.

    On Linux the holder is found through ``/proc`` (listening socket inode, then the owning
    pid and its command); elsewhere, or when ``/proc`` cannot say, the answer is generic.
    """
    family = socket.AF_INET6 if ":" in bind else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((bind, port))
        except OSError:
            pass
        else:
            return None
    return _proc_holder(port, proc) or "another process"


def _listening_inodes(port: int, proc: Path) -> set[str]:
    inodes: set[str] = set()
    for table in ("tcp", "tcp6"):
        try:
            lines = (proc / "net" / table).read_text(encoding="utf-8").splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if (
                len(fields) > 9
                and fields[3] == "0A"
                and int(fields[1].rsplit(":", 1)[1], 16) == port
            ):
                inodes.add(fields[9])
    return inodes


def _proc_holder(port: int, proc: Path) -> str | None:
    inodes = {f"socket:[{inode}]" for inode in _listening_inodes(port, proc)}
    if not inodes:
        return None
    try:
        entries = list(proc.iterdir())
    except OSError:
        return None
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            if not any(os.readlink(fd) in inodes for fd in (entry / "fd").iterdir()):
                continue
        except OSError:
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            raw = b""
        command = raw.replace(b"\0", b" ").decode("utf-8", errors="replace").strip()
        return f"pid {entry.name}" + (f" ({command})" if command else "")
    return None


class GatewayProcess(BaseModel):
    pid: int
    started_at: dt.datetime
    health_url: str
    argv: list[str]
    # Start time plus command line of the child at spawn. A pid is recycled by
    # the kernel, so stop() signals only a process that still matches this.
    identity: str | None = None


class GatewaySupervisor:
    def __init__(
        self,
        store: StateStore,
        *,
        argv: list[str] | None = None,
        health_url: str = DEFAULT_HEALTH_URL,
        env: Mapping[str, str] | None = None,
        popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        transport: httpx.BaseTransport | None = None,
        port_holder: Callable[[str, int], str | None] = port_holder,
    ) -> None:
        self._store, self._health_url = store, health_url
        self._argv = list(DEFAULT_ARGV if argv is None else argv)
        self._env = dict(os.environ if env is None else env)
        self._popen, self._transport = popen, transport
        self._port_holder = port_holder
        self._process: subprocess.Popen[bytes] | None = None

    def current(self) -> GatewayProcess | None:
        raw = self._store.read_document(GATEWAY_STATE)
        return GatewayProcess.model_validate(raw) if raw else None

    def start(self) -> GatewayProcess:
        token = self._env.get("PITWALL_GATEWAY_TOKEN")
        if not token:
            raise RuntimeError(
                "PITWALL_GATEWAY_TOKEN must be set; the gateway refuses anonymous mode"
            )
        existing = self.current()
        if existing is not None and _is_ours(existing):
            return existing
        bind, port = _listen_address(self._argv)
        holder = self._port_holder(bind, port)
        if holder is not None:
            raise GatewayPortInUse(
                f"gateway port {port} on {bind} is already in use by {holder}; "
                "stop it or free the port, then retry"
            )
        child_env = {**self._env, "PITWALL_GATEWAY_TOKEN": token}  # env only, never argv
        child_env.setdefault("PITWALL_GATEWAY_ROUTES", str(routes_path()))
        process = self._popen(
            self._argv, env=child_env, stdin=subprocess.DEVNULL, start_new_session=True
        )
        self._process = process
        record = GatewayProcess(
            pid=process.pid,
            started_at=dt.datetime.now(dt.UTC),
            health_url=self._health_url,
            argv=self._argv,
            identity=_process_identity(process.pid),
        )
        self._store.write_document(
            GATEWAY_STATE, record.model_dump(mode="json")
        )  # 0600 atomic, same as leases
        return record

    def stop(self) -> None:
        existing = self.current()
        if existing is not None and _alive(existing.pid):
            if _is_ours(existing):
                # start_new_session=True made the child its own session leader,
                # so its pid is its process-group id; no lookup on a foreign pid.
                os.killpg(existing.pid, signal.SIGTERM)
                self._reap(existing.pid)
            else:
                log.warning(
                    "gateway record pid=%s is alive but is not the process we started; "
                    "dropping the stale record without signalling it",
                    existing.pid,
                )
        self._store.remove_document(GATEWAY_STATE)

    def _reap(self, pid: int) -> None:
        """Wait for a child this supervisor started, escalating to SIGKILL after the bound."""
        process = self._process
        if process is None or process.pid != pid:
            return
        try:
            process.wait(timeout=STOP_WAIT_S)
        except subprocess.TimeoutExpired:
            os.killpg(pid, signal.SIGKILL)
            process.wait(timeout=STOP_WAIT_S)
        self._process = None

    def health(self) -> bool:
        token = self._env.get("PITWALL_GATEWAY_TOKEN", "")
        try:
            with httpx.Client(transport=self._transport, timeout=2.0) as client:
                return (
                    client.get(
                        self._health_url, headers={"Authorization": f"Bearer {token}"}
                    ).status_code
                    == 200
                )
        except httpx.HTTPError:
            return False


def _listen_address(argv: list[str]) -> tuple[str, int]:
    """The ``--bind`` and ``--port`` the argv asks for, defaulting to the gateway defaults."""
    bind, port = DEFAULT_BIND, DEFAULT_PORT
    for flag, value in zip(argv, argv[1:], strict=False):
        if flag == "--bind":
            bind = value
        elif flag == "--port" and value.isdigit():
            port = int(value)
    return bind, port


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _process_identity(pid: int, *, proc: Path | None = None) -> str | None:
    """Return ``boot:<boot_id>:start:<ticks>`` for a live pid, or None when unknowable.

    Without procfs (macOS) the identity is ``pitwall.agents.pids.process_identity``'s
    ``ps``-based start time, so ``stop()`` still recognises the gateway it started.

    ``/proc/<pid>/stat`` field 22 is the start time in clock ticks since boot, fixed at fork,
    and the kernel boot id separates boots. A recycled pid never shares both. The command line
    is deliberately not used: it is empty while the child is mid-exec, which is exactly when
    ``start()`` records it, and a process may rewrite it later.
    """
    proc = PROC_ROOT if proc is None else proc
    try:
        stat = (proc / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
        boot_id = (proc / "sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    except OSError:
        return _portable_identity(pid)
    # The comm field is parenthesised and may contain spaces; split after it.
    fields = stat.rsplit(")", 1)[-1].split()
    if len(fields) < 20:
        return _portable_identity(pid)
    return f"boot:{boot_id}:start:{fields[19]}"


def _portable_identity(pid: int) -> str | None:
    from pitwall.agents.pids import process_identity

    return process_identity(pid)


def _is_ours(record: GatewayProcess) -> bool:
    """Whether *record*'s pid is alive and still the process the supervisor started."""
    if not _alive(record.pid) or record.identity is None:
        return False
    return _process_identity(record.pid) == record.identity
