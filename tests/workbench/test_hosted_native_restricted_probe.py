"""Native child restricted probe, translated from packages/pi-workbench/tests/native-restricted-probe.test.ts.

The pinned Pi runtime delegates one worker child through the native extension under the restricted
launcher, against a loopback fixture provider that scripts the child's Bash probe.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

import pytest

from pitwall.workbench.launcher import PiLaunchOptions, extension_path, launch_pi
from pitwall.workbench.native_profile import configure_native_profile
from pitwall.workbench.profile import compile_profile
from tests.hang_guard import HANG_GUARD_SECS
from tests.workbench.pi_support import (
    FixtureServer,
    RpcClient,
    chat_chunk,
    chat_usage,
    pinned_pi,
    requires_pi,
    sse_headers,
    sse_write,
    tintin_extension,
)

HAS_PERL = shutil.which("perl") is not None and (
    subprocess.run(["perl", "-e", "exit 0"], capture_output=True, check=False).returncode == 0
)


@pytest.mark.live
@pytest.mark.parity
@requires_pi
@pytest.mark.skipif(not HAS_PERL, reason="perl is required for the io_uring probe")
@pytest.mark.skipif(
    tintin_extension() is None, reason="pinned @tintinweb/pi-subagents is not installed"
)
def test_actual_tintin_child_bash_receives_the_restricted_syscall_boundary(tmp_path: Path) -> None:
    """Source: native-restricted-probe.test.ts 'actual Tintin child Bash receives the restricted syscall boundary'."""
    cwd = tmp_path / "repository"
    subprocess.run(["git", "init", "-q", str(cwd)], check=True)
    (cwd / "seed.txt").write_text("SEED\n")
    subprocess.run(["git", "-C", str(cwd), "add", "seed.txt"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(cwd),
            "-c",
            "user.name=fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "seed",
        ],
        check=True,
    )
    outside_root = Path(tempfile.mkdtemp(prefix="pi-native-restricted-outside-", dir="/var/tmp"))
    outside = outside_root / "target.txt"
    outside.write_text("PRESERVED\n")
    requests: list[dict[str, Any]] = []
    request_urls: list[str] = []
    tool_command: list[str] = [""]

    def respond(handler: BaseHTTPRequestHandler, path: str, body: bytes) -> None:
        request_urls.append(path)
        if path != "/v1/chat/completions":
            handler.send_response(200)
            handler.send_header("content-type", "text/plain")
            handler.end_headers()
            handler.wfile.write(b"unexpected tool request")
            return
        payload = json.loads(body)
        requests.append(payload)
        names = [(tool.get("function") or {}).get("name") for tool in payload.get("tools") or []]
        parent = "agent_task" in names
        has_result = any(m.get("role") == "tool" for m in payload["messages"])
        sse_headers(handler)
        call: dict[str, Any] | None = None
        if parent and not has_result:
            call = {
                "index": 0,
                "id": "delegate-probe",
                "type": "function",
                "function": {
                    "name": "agent_task",
                    "arguments": json.dumps(
                        {
                            "role": "worker",
                            "task": "Run the restricted Bash probe and report its result.",
                        }
                    ),
                },
            }
        elif not parent and not has_result:
            call = {
                "index": 0,
                "id": "native-bash-probe",
                "type": "function",
                "function": {"name": "bash", "arguments": json.dumps({"command": tool_command[0]})},
            }
        if call:
            sse_write(handler, chat_chunk({"role": "assistant", "tool_calls": [call]}))
            sse_write(handler, chat_chunk({}, "tool_calls"))
        else:
            text = (
                "Parent received the restricted child result."
                if parent
                else "Child restricted probe completed."
            )
            sse_write(handler, chat_chunk({"role": "assistant", "content": text}))
            sse_write(handler, chat_chunk({}, "stop"))
        sse_write(handler, chat_usage(100, 8))
        sse_write(handler, "data: [DONE]\n\n")

    try:
        with FixtureServer(respond) as server:
            port = server.port
            outside_js = (
                f'require("node:fs").writeFileSync({json.dumps(str(outside))}, "CHANGED\\n")'
            )
            network_js = (
                f"fetch({json.dumps(f'http://127.0.0.1:{port}/native-tool-network')})"
                ".then(() => process.exit(0)).catch(() => { "
                'require("node:fs").writeFileSync(1, "NETWORK_DENIED\\n"); process.exit(7); })'
            )
            tool_command[0] = (
                "set -u; perl -e 'my $r=syscall(425,1,0); exit(($r < 0 && $!{EPERM}) ? 0 : 17)'; "
                "printf 'IOURING_DENIED\\n'; "
                f"node -e {json.dumps(outside_js)} || true; "
                f"node -e {json.dumps(network_js)}"
            )
            compiled = compile_profile(
                "fixture",
                {
                    "provider": "fixture",
                    "modelId": "fixture-model",
                    "endpoint": server.url,
                    "api": "openai-completions",
                    "keyless": "dummy",
                    "reasoningLevel": "off",
                    "servedContextTokens": 32768,
                    "maxCompletionTokens": 4096,
                    "resourceGroup": "fixture",
                    "allowProviderFallback": False,
                },
                tmp_path / "agent",
            )
            native = configure_native_profile(compiled, cwd)
            tintin = tintin_extension()
            assert tintin is not None
            client = RpcClient(
                launch_pi(
                    PiLaunchOptions(
                        cwd=cwd,
                        profile=compiled,
                        pi_bin=pinned_pi(),
                        env=native.env,
                        extensions=[tintin, extension_path("native-extension")],
                        restricted=True,
                        runtime_dir=tmp_path / "runtime",
                    )
                )
            )
            try:
                client.send(
                    {"type": "prompt", "id": "probe", "message": "Delegate the worker probe."}
                )
                client.until(
                    lambda: client.settled_count() >= 1,
                    timeout=HANG_GUARD_SECS,
                    what="the probe to settle",
                )
                assert outside.read_text() == "PRESERVED\n"
                assert request_urls.count("/v1/chat/completions") >= 2
                assert "/native-tool-network" not in request_urls
                tool_result = next(
                    (
                        message
                        for payload in requests
                        for message in payload["messages"]
                        if message.get("role") == "tool"
                        and message.get("tool_call_id") == "native-bash-probe"
                    ),
                    None,
                )
                assert tool_result is not None, json.dumps(requests)
                rendered = json.dumps(tool_result)
                assert "IOURING_DENIED" in rendered
                assert "NETWORK_DENIED" in rendered
                assert re.search(r"EROFS|read-only|EACCES|EPERM", rendered, re.IGNORECASE)
                assert not any(e.get("type") == "extension_error" for e in client.snapshot())
            finally:
                client.close()
    finally:
        shutil.rmtree(outside_root, ignore_errors=True)
