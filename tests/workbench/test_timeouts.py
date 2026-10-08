"""Timeout tests, translated from packages/pi-workbench/tests/timeouts.test.ts."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from pitwall.workbench.timeouts import (
    ResponseIdleTimeout,
    apply_solo_tool_deadline,
    with_response_idle_timeout,
)
from tests.hang_guard import HANG_GUARD_SECS
from tests.workbench.pi_support import pinned_pi, requires_pi


@pytest.mark.parity
def test_solo_tool_deadline_caps_long_or_absent_shell_timeouts_while_preserving_shorter_requests() -> (
    None
):
    """Source: timeouts.test.ts 'solo tool deadline caps long or absent shell timeouts while preserving shorter requests'."""
    absent: dict[str, Any] = {"toolName": "bash", "input": {"command": "sleep 1"}}
    assert apply_solo_tool_deadline(absent, 1_500) is True
    assert absent["input"]["timeout"] == 1.5
    long: dict[str, Any] = {
        "toolName": "powershell",
        "input": {"command": "Start-Sleep 1", "timeout": 10},
    }
    assert apply_solo_tool_deadline(long, 1_500) is True
    assert long["input"]["timeout"] == 1.5
    short: dict[str, Any] = {"toolName": "bash", "input": {"command": "true", "timeout": 0.5}}
    assert apply_solo_tool_deadline(short, 1_500) is False
    assert short["input"]["timeout"] == 0.5
    unrelated: dict[str, Any] = {"toolName": "read", "input": {"path": "x"}}
    assert apply_solo_tool_deadline(unrelated, 1_500) is False
    assert "timeout" not in unrelated["input"]


def test_solo_tool_deadline_is_inert_without_a_profile_timeout() -> None:
    event: dict[str, Any] = {"toolName": "bash", "input": {"command": "true"}}
    assert apply_solo_tool_deadline(event, None) is False
    assert "timeout" not in event["input"]


BASH_PROBE = """
import { pathToFileURL } from "node:url";
const { createBashToolDefinition } = await import(pathToFileURL(process.argv[2]).href);
const input = JSON.parse(process.argv[3]);
const tool = createBashToolDefinition(process.cwd(), { exposeSessionEnvironment: false });
const started = Date.now();
let message = "";
try {
  await tool.execute("timeout-test", input, new AbortController().signal, undefined, { cwd: process.cwd() });
} catch (error) { message = String(error?.message ?? error); }
process.stdout.write(JSON.stringify({ message, elapsedMs: Date.now() - started }));
"""


@pytest.mark.live
@pytest.mark.parity
@requires_pi
def test_pinned_pi_bash_tool_honors_the_mutated_deadline_and_kills_a_long_command(
    tmp_path: Path,
) -> None:
    """Source: timeouts.test.ts 'pinned Pi bash tool honors the mutated deadline and kills a long command'."""
    pi_bin = pinned_pi()
    assert pi_bin is not None
    entry = Path(pi_bin).resolve().parents[1] / "index.js"
    node = shutil.which("node")
    assert node is not None
    tool_input: dict[str, Any] = {"command": f'{node} -e "setTimeout(() => {{}}, 5000)"'}
    assert apply_solo_tool_deadline({"toolName": "bash", "input": tool_input}, 100) is True
    probe = tmp_path / "probe.mjs"
    probe.write_text(BASH_PROBE)
    completed = subprocess.run(
        [node, str(probe), str(entry), json.dumps(tool_input)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=HANG_GUARD_SECS,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert "Command timed out" in result["message"]
    assert result["elapsedMs"] < 1500


async def stalls_after_headers() -> AsyncIterator[bytes]:
    yield b"first"
    await asyncio.sleep(30)
    yield b"never"


@pytest.mark.parity
async def test_response_idle_wrapper_aborts_a_body_that_stops_after_headers() -> None:
    """Source: timeouts.test.ts 'response idle wrapper aborts a body that stops after headers'."""
    body = with_response_idle_timeout(stalls_after_headers(), 50)
    received: list[bytes] = []
    with pytest.raises(ResponseIdleTimeout, match="inactivity timeout"):
        async for chunk in body:
            received.append(chunk)
    assert received == [b"first"]


@pytest.mark.parity
async def test_response_idle_wrapper_applies_backpressure_instead_of_eagerly_buffering_the_body() -> (
    None
):
    """Source: timeouts.test.ts 'response idle wrapper applies backpressure instead of eagerly buffering the body'."""
    pulls = 0

    async def upstream() -> AsyncIterator[bytes]:
        nonlocal pulls
        while pulls < 1_000:
            pulls += 1
            yield bytes(1024)

    body = aiter(with_response_idle_timeout(upstream(), 100))
    await asyncio.sleep(0.03)
    assert pulls == 0  # nothing is pulled until the consumer reads
    await anext(body)
    assert pulls == 1
    await body.aclose()  # type: ignore[attr-defined]  # reason: the wrapper is an async generator
    assert pulls == 1


async def test_response_idle_wrapper_is_transparent_when_disabled_or_fast() -> None:
    async def quick() -> AsyncIterator[bytes]:
        for part in (b"a", b"b"):
            yield part

    source = quick()
    assert with_response_idle_timeout(source, None) is source
    assert with_response_idle_timeout(source, 0) is source
    assert [chunk async for chunk in with_response_idle_timeout(quick(), 500)] == [b"a", b"b"]
