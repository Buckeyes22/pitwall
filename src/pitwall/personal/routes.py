"""Attach, probe, and remove route profiles through the `pitwall agents` CLI."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from pitwall.security.redaction import redact_text

_TIMEOUT_S = 30
_DETAIL_MAX = 160
_DEFAULT_SEAT = "local"

DEFAULT_ROUTING_CLI = "pitwall agents"

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def routing_command(cli: str, env: Mapping[str, str]) -> list[str]:
    """Expand the routing command setting into an argv prefix.

    The setting may be several tokens (`pitwall agents`). A leading `pitwall` resolves to the
    installed console script on the subprocess search path, else to this interpreter's own
    `-m pitwall`, so a clean install never depends on any other executable being present.
    """
    tokens = shlex.split(cli) or [DEFAULT_ROUTING_CLI.split()[0]]
    if tokens[0] != "pitwall":
        return tokens
    found = shutil.which("pitwall", path=os.pathsep.join(os.get_exec_path(dict(env))))
    launcher = [found] if found else [sys.executable, "-m", "pitwall"]
    return [*launcher, *tokens[1:]]


def routing_available(cli: str, env: Mapping[str, str]) -> bool:
    """Whether the routing command resolves on the same search path its subprocesses use."""
    head = routing_command(cli, env)[0]
    path = os.pathsep.join(os.get_exec_path(dict(env)))
    return shutil.which(head, path=path) is not None


@dataclass(frozen=True, slots=True)
class RouteOutcome:
    ok: bool
    action: Literal["added", "probed", "removed", "failed"]
    detail: str


class RouteRunner:
    def __init__(self, cli: str, *, env: Mapping[str, str], run: Runner = subprocess.run) -> None:
        self._cli = cli
        self._env = dict(env)
        self._run = run

    def attach(
        self,
        route: str,
        *,
        base_url: str,
        model_id: str,
        key_env: str,
        seat: str = _DEFAULT_SEAT,
    ) -> RouteOutcome:
        return self._invoke(
            "added",
            [
                "profiles",
                "add",
                route,
                "--base-url",
                base_url,
                "--model",
                model_id,
                "--api-key-env",
                key_env,
                "--seat",
                seat,
            ],
        )

    def probe(self, route: str) -> RouteOutcome:
        return self._invoke("probed", ["profiles", "probe", route])

    def remove(self, route: str) -> RouteOutcome:
        return self._invoke("removed", ["profiles", "remove", route])

    def available(self) -> bool:
        """Resolve the CLI on the same search path its subprocesses will use."""
        return routing_available(self._cli, self._env)

    def exists(self, route: str) -> bool:
        try:
            result = self._run(
                [*routing_command(self._cli, self._env), "profiles", "show", route],
                env=self._env,
                capture_output=True,
                text=True,
                check=False,
                timeout=_TIMEOUT_S,
            )
        except OSError, subprocess.TimeoutExpired:
            return False
        return result.returncode == 0

    def _invoke(
        self, action: Literal["added", "probed", "removed"], args: list[str]
    ) -> RouteOutcome:
        try:
            result = self._run(
                [*routing_command(self._cli, self._env), *args],
                env=self._env,
                capture_output=True,
                text=True,
                check=False,
                timeout=_TIMEOUT_S,
            )
        except FileNotFoundError:
            return RouteOutcome(
                ok=False, action="failed", detail=f"routing cli not found: {self._cli}"
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return RouteOutcome(ok=False, action="failed", detail=type(exc).__name__)
        if result.returncode == 0:
            return RouteOutcome(ok=True, action=action, detail="")
        detail = redact_text(" ".join(result.stderr.split()))[:_DETAIL_MAX]
        return RouteOutcome(ok=False, action="failed", detail=detail)
