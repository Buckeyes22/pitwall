"""Helpers for exercising the public Bash shim contract in isolated sandboxes."""

from __future__ import annotations

import atexit
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from pitwall.agents.installation import shim_names, shim_script
from tests.agents.profiles_fixture import write_profiles
from tests.hang_guard import HANG_GUARD_SECS, reap

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/agents" / "fixtures" / "fake_harness.py"
_SHIM_DIR = Path(tempfile.mkdtemp(prefix="pitwall-generated-shims-"))
atexit.register(shutil.rmtree, _SHIM_DIR, ignore_errors=True)


def _generated_shim(name: str) -> Path:
    path = _SHIM_DIR / name
    path.write_text(shim_script(name), encoding="utf-8")
    path.chmod(0o755)
    return path


def _write_pitwall_stub(directory: Path) -> Path:
    """A `pitwall` command that runs this checkout's agents runtime.

    `pitwall agents <args>` and `pitwall mcp serve channel` map onto the interpreter running the
    suite, so the tests exercise this checkout and never an installed copy.
    """

    directory.mkdir(parents=True, exist_ok=True)
    stub = directory / "pitwall"
    stub.write_text(
        "#!/bin/sh\n"
        'if [ "$1 $2 $3" = "mcp serve channel" ]; then shift 3; '
        f'exec "{sys.executable}" -m pitwall.agents mcp "$@"; fi\n'
        '[ "$1" = agents ] && shift\n'
        f'exec "{sys.executable}" -m pitwall.agents "$@"\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return stub


PITWALL = _write_pitwall_stub(_SHIM_DIR / "bin")

# The shims under test are the ones `pitwall agents install` writes, not committed files.
ROUTE_SHIM = _generated_shim("route-shim.sh")
SHIMS = {
    name.removesuffix("-shim.sh"): _generated_shim(name)
    for name in shim_names()
    if name != "route-shim.sh"
}

SUPPORT_COMMANDS = ("cat", "date", "dirname", "git", "head", "mkdir", "sed")


class ShimSandbox:
    def __init__(self) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="pitwall-agents-shim-test-")
        self._processes: list[subprocess.Popen[Any]] = []
        self.root = Path(self._temp.name)
        self.home = self.root / "home"
        self.bin = self.root / "bin"
        self.home.mkdir()
        self.bin.mkdir()
        self.ledger = self.root / "ledger" / "observations.jsonl"
        self.state = self.root / "state"
        self.config = self.root / "config"
        self.args_file = self.root / "args.bin"
        self.stdin_file = self.root / "stdin.bin"
        self.env_file = self.root / "env.json"
        for command in SUPPORT_COMMANDS:
            self._link_system_command(command)
        self._link_interpreter()
        self._install_pitwall_stub()

    def popen(self, argv: list[str], **kwargs: Any) -> subprocess.Popen[Any]:
        """Start a child in its own session and own it: `cleanup` kills its whole process group.

        A failing or timed-out test therefore never leaves a shim or harness writing into the
        sandbox while it is removed, nor open pipes for a later test to finalise.
        """

        kwargs.setdefault("start_new_session", True)
        process = subprocess.Popen(argv, **kwargs)  # noqa: S603  # reason: argv is built by the test
        self._processes.append(process)
        return process

    def cleanup(self) -> None:
        while self._processes:
            reap(self._processes.pop(), group=True)
        # Anything the sandbox does not own may still be finishing a write; retry, then give up
        # quietly rather than raise from rmtree and mask the failure the test already reported.
        for _ in range(100):
            try:
                self._temp.cleanup()
                return
            except OSError:
                time.sleep(0.1)
        shutil.rmtree(self.root, ignore_errors=True)

    def _link_system_command(self, command: str) -> None:
        source = shutil.which(command)
        if not source:
            raise RuntimeError(f"required test command not found: {command}")
        (self.bin / command).symlink_to(source)

    def _link_interpreter(self) -> None:
        # The shims exec a python3 to reach the shared runtime, so the sandbox
        # has to supply one exactly as it supplies its other commands. Link the
        # interpreter running the suite rather than resolving python3 from the
        # host: `command -v python3` finds nothing on this PATH, so the shim
        # would fall back to /usr/bin/python3, which is 3.12 on the Linux
        # runners but 3.9.6 on every macOS. That difference decided whether
        # these tests exercised the shim contract or the runtime's version
        # guard, purely by host.
        # A bare symlink loses the virtualenv (pyvenv.cfg is found next to the
        # invoked path), and the runtime now uses the project's shared
        # dependencies (httpx, pydantic), so exec the suite's interpreter.
        wrapper = self.bin / "python3"
        wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
        wrapper.chmod(0o755)

    def _install_pitwall_stub(self) -> None:
        # The generated shims run `pitwall agents _shim ...`; give the sandbox that command.
        shutil.copy2(PITWALL, self.bin / "pitwall")

    def install_harness(self, harness: str) -> Path:
        target = self.bin / harness
        shutil.copy2(FIXTURE, target)
        target.chmod(0o755)
        return target

    def prompt(self, text: str = "test prompt\n") -> Path:
        target = self.root / "prompt.md"
        target.write_text(text, encoding="utf-8")
        return target

    def write_routes(self, data: dict[str, Any]) -> Path:
        return write_profiles(self.config / "pitwall" / "pitwall.toml", data)

    def environment(self, **overrides: str) -> dict[str, str]:
        env = {
            "HOME": str(self.home),
            "PATH": str(self.bin),
            "PITWALL_AGENTS_LEDGER": str(self.ledger),
            "XDG_STATE_HOME": str(self.state),
            "XDG_CONFIG_HOME": str(self.config),
            "PYTHONPYCACHEPREFIX": str(self.root / "pycache"),
            "FAKE_ARGS_FILE": str(self.args_file),
            "FAKE_STDIN_FILE": str(self.stdin_file),
            "FAKE_ENV_FILE": str(self.env_file),
        }
        env.update(overrides)
        return env

    def run(
        self,
        shim: str,
        args: list[str],
        *,
        input_bytes: bytes | None = None,
        env: dict[str, str] | None = None,
        timeout: float = HANG_GUARD_SECS,
    ) -> subprocess.CompletedProcess[bytes]:
        return self._run(
            ["/bin/bash", str(SHIMS[shim]), *args], input_bytes, env or self.environment(), timeout
        )

    def _run(
        self, argv: list[str], input_bytes: bytes | None, env: dict[str, str], timeout: float
    ) -> subprocess.CompletedProcess[bytes]:
        process = self.popen(
            argv,
            stdin=subprocess.PIPE if input_bytes is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        try:
            stdout, stderr = process.communicate(input_bytes, timeout=timeout)
        except BaseException:
            reap(process, group=True)
            raise
        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)

    def run_route(
        self,
        args: list[str],
        *,
        input_bytes: bytes | None = None,
        env: dict[str, str] | None = None,
        timeout: float = HANG_GUARD_SECS,
    ) -> subprocess.CompletedProcess[bytes]:
        return self._run(
            ["/bin/bash", str(ROUTE_SHIM), *args], input_bytes, env or self.environment(), timeout
        )

    def ledger_records(self) -> list[dict[str, Any]]:
        if not self.ledger.exists():
            return []
        return [
            json.loads(line)
            for line in self.ledger.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def captured_args(self) -> list[str]:
        if not self.args_file.exists() or not self.args_file.read_bytes():
            return []
        return [part.decode() for part in self.args_file.read_bytes().split(b"\0")]

    def captured_stdin(self) -> bytes:
        return self.stdin_file.read_bytes() if self.stdin_file.exists() else b""

    def captured_env(self) -> dict[str, str | None]:
        if not self.env_file.exists():
            return {}
        captured: dict[str, str | None] = json.loads(self.env_file.read_text(encoding="utf-8"))
        return captured

    def run_directories(self) -> list[Path]:
        root = self.state / "pitwall" / "agents" / "runs"
        return list(root.iterdir()) if root.is_dir() else []
