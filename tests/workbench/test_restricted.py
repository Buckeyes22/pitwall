"""Restricted-mode policy tests, translated from packages/pi-workbench/tests.

Argv and policy cases run everywhere; cases that need a real sandbox skip with a reason.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

import pytest

from pitwall.workbench.launcher import PiLaunchOptions, launch_pi
from pitwall.workbench.profile import CompiledProfile, compile_profile, configure_provider_profile
from pitwall.workbench.restricted import (
    RestrictedHost,
    RestrictedModeError,
    RestrictedPolicy,
    assert_restricted_prerequisites,
    build_restricted_command,
    restricted_tool_environment,
    run_restricted_tool,
    setpriv_supports_seccomp_filter,
)
from tests.hang_guard import HANG_GUARD_SECS
from tests.workbench.pi_support import (
    FixtureServer,
    RpcClient,
    chat_chunk,
    chat_usage,
    pinned_pi,
    sse_headers,
    sse_write,
)

# The sandbox hides /home, so real-sandbox cases need an interpreter outside it.
SYSTEM_PYTHON = "/usr/bin/python3"

PRESENT = RestrictedHost(
    system="linux",
    machine="x86_64",
    path_exists=lambda _path: True,
    setpriv_supports_seccomp_filter=lambda: True,
)


def make_dirs(root: Path, *names: str) -> list[Path]:
    paths = [root / name for name in names]
    for path in paths:
        path.mkdir()
    return paths


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, RestrictedPolicy]:
    cwd, agent, runtime, admission = make_dirs(tmp_path, "work", "agent", "runtime", "admission")
    return cwd, RestrictedPolicy(agent_dir=agent, runtime_root=runtime, admission_dir=admission)


def value_after(args: list[str], flag: str, target: str) -> bool:
    return any(args[i] == flag and args[i + 1] == target for i in range(len(args) - 1))


def sandbox_usable() -> str | None:
    """Return a skip reason when bubblewrap cannot start a sandbox here."""
    if platform.system() != "Linux" or not shutil.which("bwrap"):
        return "bubblewrap is not installed"
    if not Path(SYSTEM_PYTHON).exists():
        return "/usr/bin/python3 is absent"
    if not Path("/usr/bin/bwrap").exists():
        return "/usr/bin/bwrap is absent"
    probe = subprocess.run(  # noqa: S603  # reason: fixed bubblewrap capability probe
        ["/usr/bin/bwrap", "--ro-bind", "/", "/", "true"], capture_output=True, check=False
    )
    return None if probe.returncode == 0 else "bubblewrap cannot create a sandbox on this host"


def seccomp_usable() -> str | None:
    if not setpriv_supports_seccomp_filter():
        return "/usr/bin/setpriv lacks --seccomp-filter (util-linux 2.41+ required)"
    return None


# --- restricted-prerequisites.test.ts -------------------------------------------------


@pytest.mark.parity
def test_refuses_setpriv_without_seccomp_filter_before_launch() -> None:
    """Source: restricted-prerequisites.test.ts 'restricted mode refuses a setpriv without --seccomp-filter'."""
    old = RestrictedHost(
        system="linux",
        machine="x86_64",
        path_exists=lambda _p: True,
        setpriv_supports_seccomp_filter=lambda: False,
    )
    with pytest.raises(RestrictedModeError, match=r"util-linux 2\.41 or later"):
        assert_restricted_prerequisites(old)
    assert_restricted_prerequisites(PRESENT)


@pytest.mark.parity
def test_seccomp_filter_support_read_from_binary_without_running_it() -> None:
    """Source: restricted-prerequisites.test.ts 'seccomp filter support is read from the setpriv binary'."""
    assert setpriv_supports_seccomp_filter("/f", lambda _p: b"usage ... --seccomp-filter <file>")
    assert not setpriv_supports_seccomp_filter("/f", lambda _p: b"usage ... --landlock-access")

    def missing(_path: str) -> bytes:
        raise FileNotFoundError("ENOENT")

    assert not setpriv_supports_seccomp_filter("/f", missing)


def test_refuses_without_bubblewrap_and_never_falls_back(
    dirs: tuple[Path, RestrictedPolicy],
) -> None:
    cwd, policy = dirs
    no_bwrap = RestrictedHost(
        system="linux",
        machine="x86_64",
        path_exists=lambda p: p != "/usr/bin/bwrap",
        setpriv_supports_seccomp_filter=lambda: True,
    )
    with pytest.raises(RestrictedModeError, match="bubblewrap; refusing an unrestricted fallback"):
        build_restricted_command(["/bin/true"], cwd, policy, host=no_bwrap)


def test_refuses_non_linux_missing_setpriv_and_unsupported_arch() -> None:
    def host(**over: object) -> RestrictedHost:
        base = {
            "system": "linux",
            "machine": "x86_64",
            "path_exists": lambda _p: True,
            "setpriv_supports_seccomp_filter": lambda: True,
        }
        return RestrictedHost(**{**base, **over})  # type: ignore[arg-type]  # reason: test helper merges keyword overrides

    with pytest.raises(RestrictedModeError, match="requires Linux; refusing"):
        assert_restricted_prerequisites(host(system="darwin"))
    with pytest.raises(RestrictedModeError, match="/usr/bin/setpriv"):
        assert_restricted_prerequisites(host(path_exists=lambda p: p != "/usr/bin/setpriv"))
    with pytest.raises(RestrictedModeError, match="unsupported on riscv64"):
        assert_restricted_prerequisites(host(machine="riscv64"))


# --- restricted.ts policy (argv) -------------------------------------------------------


@pytest.mark.parity
def test_mounts_hide_home_root_run_and_tmp(dirs: tuple[Path, RestrictedPolicy]) -> None:
    """Source: restricted.ts restrictedCommand mounts, pinned by native-restricted-probe boundary test."""
    cwd, policy = dirs
    argv = build_restricted_command([sys.executable, "-c", "pass"], cwd, policy, host=PRESENT)
    assert argv[0] == "/usr/bin/bwrap"
    assert value_after(argv, "--ro-bind", "/")
    for hidden in ("/home", "/root", "/run", "/tmp"):  # noqa: S108  # reason: asserting sandbox tmpfs targets
        assert value_after(argv, "--tmpfs", hidden)
    for flag in (
        "--die-with-parent",
        "--new-session",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
    ):
        assert flag in argv
    assert argv[argv.index("--chdir") + 1] == str(cwd.resolve())
    assert argv[argv.index("--") + 1 :] == [sys.executable, "-c", "pass"]


@pytest.mark.parity
def test_hoisted_runtime_dependencies_mounted_read_only(tmp_path: Path) -> None:
    """Source: native-restricted-probe.test.ts 'mounts hoisted runtime dependencies read-only' (argv part)."""
    cwd, agent, runtime, dependency, admission = make_dirs(
        tmp_path, "work", "agent", "runtime", "dependencies", "admission"
    )
    policy = RestrictedPolicy(
        agent_dir=agent,
        runtime_root=runtime,
        admission_dir=admission,
        runtime_dependencies=[dependency],
    )
    argv = build_restricted_command(["/bin/true"], cwd, policy, host=PRESENT)
    assert value_after(argv, "--ro-bind", str(dependency.resolve()))
    assert value_after(argv, "--ro-bind", str(runtime.resolve()))
    for writable in (cwd, agent, admission):
        assert value_after(argv, "--bind", str(writable.resolve()))
    assert not value_after(argv, "--bind", str(dependency.resolve()))


def test_account_budget_dir_is_writable(
    dirs: tuple[Path, RestrictedPolicy], tmp_path: Path
) -> None:
    cwd, policy = dirs
    (budget,) = make_dirs(tmp_path, "account")
    with_budget = RestrictedPolicy(
        agent_dir=policy.agent_dir,
        runtime_root=policy.runtime_root,
        admission_dir=policy.admission_dir,
        account_budget_dir=budget,
    )
    argv = build_restricted_command(["/bin/true"], cwd, with_budget, host=PRESENT)
    assert value_after(argv, "--bind", str(budget.resolve()))


@pytest.mark.parametrize("forbidden", ["/", "/home", "/root"])
def test_refuses_root_home_and_root_home_as_workdir(
    forbidden: str, dirs: tuple[Path, RestrictedPolicy]
) -> None:
    _, policy = dirs
    if not Path(forbidden).exists():
        pytest.skip(f"{forbidden} does not exist on this host")
    try:
        os.stat(forbidden)
    except PermissionError:
        pytest.skip(f"{forbidden} is not resolvable by this user")
    with pytest.raises(RestrictedModeError, match="specific owned directories"):
        build_restricted_command(["/bin/true"], Path(forbidden), policy, host=PRESENT)


def test_refuses_forbidden_mount_via_any_policy_directory(
    dirs: tuple[Path, RestrictedPolicy],
) -> None:
    cwd, policy = dirs
    bad = RestrictedPolicy(
        agent_dir=policy.agent_dir, runtime_root=Path("/"), admission_dir=policy.admission_dir
    )
    with pytest.raises(RestrictedModeError, match="specific owned directories"):
        build_restricted_command(["/bin/true"], cwd, bad, host=PRESENT)


def test_empty_argv_rejected(dirs: tuple[Path, RestrictedPolicy]) -> None:
    cwd, policy = dirs
    with pytest.raises(ValueError, match="non-empty argv"):
        build_restricted_command([], cwd, policy, host=PRESENT)


# --- environment -----------------------------------------------------------------------


@pytest.mark.parity
def test_tool_child_cannot_see_selected_or_conventional_credentials() -> None:
    """Source: native-restricted-probe.test.ts 'restricted tool child cannot print ... credentials' (env part)."""
    env = {
        "PATH": "/usr/bin",
        "SELECTED_PROVIDER_KEY": "restricted-child-secret-sentinel",  # pragma: allowlist secret
        "UNRELATED_ACCOUNT_KEY": "unrelated-secret-sentinel",  # pragma: allowlist secret
        "HOME": "/home/x",
        "GITHUB_TOKEN": "t",
        "MY_PASSWORD": "p",
        "KEYBOARD": "kept",
        "OPENAI_API_KEY": "k",
        "AUTHORIZATION": "a",
    }
    cleaned = restricted_tool_environment(env)
    assert cleaned == {"PATH": "/usr/bin", "HOME": "/home/x", "KEYBOARD": "kept"}
    assert restricted_tool_environment({"CUSTOM": "v", "PATH": "p"}, "CUSTOM") == {"PATH": "p"}
    assert env["GITHUB_TOKEN"] == "t"  # input is not mutated


# --- real sandbox ----------------------------------------------------------------------


@pytest.mark.parity
@pytest.mark.skipif(sandbox_usable() is not None, reason=sandbox_usable() or "")
def test_actual_boundary_permits_owned_work_and_denies_outside_writes(
    tmp_path: Path,
) -> None:
    """Source: native-restricted-probe.test.ts 'actual OS boundary permits owned work and denies outside writes'."""
    cwd, agent, runtime, admission = make_dirs(tmp_path, "work", "agent", "runtime", "admission")
    outside_dir = Path("/var/tmp") / f"pitwall-boundary-outside-{os.getpid()}"  # noqa: S108  # reason: intentionally outside the sandbox mounts
    outside_dir.mkdir()
    outside = outside_dir / "outside"
    outside.write_text("preserved")
    code = (
        f"import pathlib,sys\n"
        f"pathlib.Path({str(cwd / 'owned')!r}).write_text('ok')\n"
        f"try:\n pathlib.Path({str(outside)!r}).write_text('overwritten')\n"
        f"except OSError:\n pass\n"
        f"else:\n sys.exit(3)\n"
        f"if pathlib.Path.home().joinpath('.config/opencode/opencode.json').exists(): sys.exit(4)\n"
    )
    policy = RestrictedPolicy(agent_dir=agent, runtime_root=runtime, admission_dir=admission)
    try:
        argv = build_restricted_command([SYSTEM_PYTHON, "-c", code], cwd, policy, host=PRESENT)
        result = subprocess.run(argv, capture_output=True, text=True, check=False)  # noqa: S603  # reason: argv built by the code under test
        assert result.returncode == 0, result.stderr
        assert outside.read_text() == "preserved"
        assert (cwd / "owned").read_text() == "ok"
    finally:
        shutil.rmtree(outside_dir, ignore_errors=True)


@pytest.mark.parity
@pytest.mark.skipif(sandbox_usable() is not None, reason=sandbox_usable() or "")
def test_actual_boundary_denies_writes_to_hoisted_dependencies(tmp_path: Path) -> None:
    """Source: native-restricted-probe.test.ts 'restricted boundary mounts hoisted runtime dependencies read-only'."""
    cwd, agent, runtime, dependency, admission = make_dirs(
        tmp_path, "work", "agent", "runtime", "dependencies", "admission"
    )
    target = dependency / "dependency.mjs"
    original = "hoisted-runtime-dependency\n"
    target.write_text(original)
    code = (
        "import pathlib,sys\n"
        f"p=pathlib.Path({str(target)!r})\n"
        "try:\n p.write_text('changed\\n'); sys.exit(3)\n"
        "except OSError as e:\n"
        " import errno\n"
        " if e.errno not in (errno.EROFS, errno.EACCES, errno.EPERM): sys.exit(4)\n"
        f" if p.read_text() != {original!r}: sys.exit(5)\n"
    )
    policy = RestrictedPolicy(
        agent_dir=agent,
        runtime_root=runtime,
        admission_dir=admission,
        runtime_dependencies=[dependency],
    )
    argv = build_restricted_command([SYSTEM_PYTHON, "-c", code], cwd, policy, host=PRESENT)
    result = subprocess.run(
        argv, capture_output=True, text=True, timeout=HANG_GUARD_SECS, check=False
    )  # noqa: S603  # reason: argv built by the code under test
    assert result.returncode == 0, result.stderr
    assert target.read_text() == original


@pytest.mark.parity
@pytest.mark.skipif(seccomp_usable() is not None, reason=seccomp_usable() or "")
def test_tool_filter_is_passed_by_unlinked_fd_across_sequential_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Source: native-restricted-probe.test.ts 'restricted tool filter is passed by unlinked fd across sequential tool calls'."""
    # Any temporary file the runner made would land in this test's own temporary directory,
    # so the tamper probe walks that tree instead of the host's shared /tmp, whose size
    # (hundreds of thousands of entries) would otherwise decide whether the walk fits the limit.
    own_tmp = tmp_path / "tmp"
    own_tmp.mkdir()
    monkeypatch.setenv("TMPDIR", str(own_tmp))
    monkeypatch.setattr(tempfile, "tempdir", str(own_tmp))
    first = run_restricted_tool(
        f"find {shlex.quote(str(own_tmp))} -maxdepth 3 -type f -name deny-network.bpf"
        " -exec sh -c 'printf TAMPERED > \"$1\"' _ {} \\; ; printf TAMPER_ATTEMPTED",
        tmp_path,
        timeout=HANG_GUARD_SECS,
    )
    assert first.exit_code == 0
    assert b"TAMPER_ATTEMPTED" in first.output
    assert b"deny-network.bpf" not in first.output
    probe = (
        "import socket,sys\n"
        "try:\n socket.socket().connect(('127.0.0.1', 1))\n"
        "except OSError:\n print('NETWORK_DENIED'); sys.exit(7)\n"
        "sys.exit(0)\n"
    )
    second = run_restricted_tool(
        f'{sys.executable} -c "{probe}"', tmp_path, timeout=HANG_GUARD_SECS
    )
    assert second.exit_code == 7
    assert b"NETWORK_DENIED" in second.output


@pytest.mark.parity
@pytest.mark.skipif(seccomp_usable() is not None, reason=seccomp_usable() or "")
def test_tool_child_cannot_print_credentials(tmp_path: Path) -> None:
    """Source: native-restricted-probe.test.ts 'restricted tool child cannot print selected or conventional provider credentials'."""
    result = run_restricted_tool(
        "printf 'selected=%s unrelated=%s path=%s\\n' \"${SELECTED_PROVIDER_KEY-ABSENT}\" "
        '"${UNRELATED_ACCOUNT_KEY-ABSENT}" "${PATH:+present}"',
        tmp_path,
        env={
            "PATH": os.environ["PATH"],
            "SELECTED_PROVIDER_KEY": "restricted-child-secret-sentinel",  # pragma: allowlist secret
            "UNRELATED_ACCOUNT_KEY": "unrelated-secret-sentinel",  # pragma: allowlist secret
        },
        credential_env="SELECTED_PROVIDER_KEY",
        timeout=HANG_GUARD_SECS,
    )
    assert result.exit_code == 0
    assert result.output == b"selected=ABSENT unrelated=ABSENT path=present\n"


@pytest.mark.parity
@pytest.mark.skipif(seccomp_usable() is not None, reason=seccomp_usable() or "")
@pytest.mark.skipif(shutil.which("perl") is None, reason="perl is not installed")
def test_tool_filter_denies_io_uring_setup(tmp_path: Path) -> None:
    """Source: native-restricted-probe.test.ts 'actual restricted tool filter denies io_uring setup'."""
    if platform.machine() not in ("x86_64", "amd64"):
        pytest.skip("syscall 425 is io_uring_setup on x86_64 only in this probe")
    result = run_restricted_tool(
        "set -eu; perl -e 'my $r=syscall(425,1,0); exit(($r < 0 && $!{EPERM}) ? 0 : 17)'; printf IOURING_DENIED",
        tmp_path,
        timeout=HANG_GUARD_SECS,
    )
    assert result.exit_code == 0
    assert b"IOURING_DENIED" in result.output


def pinned_pi_restricted_unavailable() -> str | None:
    """Skip reason for the live cases that run a real pinned Pi inside the sandbox."""
    if pinned_pi() is None or shutil.which("node") is None:
        return "needs node and a pinned pi binary"
    return sandbox_usable() or seccomp_usable()


def restricted_options(
    root: Path, compiled: CompiledProfile, env: dict[str, str]
) -> PiLaunchOptions:
    """Restricted launch options whose admission state and staged runtime stay under ``root``."""
    return PiLaunchOptions(
        cwd=root,
        profile=compiled,
        pi_bin=pinned_pi(),
        env={
            "PITWALL_WORKBENCH_RESOURCE_DIR": str(root / "admission"),
            "PITWALL_WORKBENCH_ACCOUNT_BUDGET_DIR": str(root / "account"),
            **env,
        },
        restricted=True,
        runtime_dir=root / "runtime",
    )


@pytest.mark.live
@pytest.mark.parity
@pytest.mark.skipif(
    pinned_pi_restricted_unavailable() is not None, reason=pinned_pi_restricted_unavailable() or ""
)
def test_actual_pinned_pi_starts_inside_restricted_boundary(tmp_path: Path) -> None:
    """Source: restricted.test.ts 'actual pinned Pi starts inside restricted boundary with selected model'."""
    profile = {
        "provider": "fixture",
        "modelId": "fixture-model",
        "endpoint": "http://127.0.0.1:1/v1",
        "api": "openai-completions",
        "keyless": "dummy",
        "servedContextTokens": 32768,
        "maxCompletionTokens": 4096,
        "resourceGroup": "restricted-fixture",
        "allowProviderFallback": False,
    }
    compiled = compile_profile("restricted", profile, tmp_path / "agent")
    client = RpcClient(launch_pi(restricted_options(tmp_path, compiled, {}), rpc=True))
    try:
        client.send({"type": "get_state", "id": "state"})
        client.until(
            lambda: any(e.get("command") == "get_state" for e in client.snapshot()),
            timeout=HANG_GUARD_SECS,
            what="restricted get_state",
        )
        assert '"id":"fixture-model"' in json.dumps(client.snapshot(), separators=(",", ":"))
    finally:
        client.close()


@pytest.mark.live
@pytest.mark.parity
@pytest.mark.skipif(
    pinned_pi_restricted_unavailable() is not None, reason=pinned_pi_restricted_unavailable() or ""
)
def test_actual_pinned_pi_child_bash_path_violation_is_denied(tmp_path: Path) -> None:
    """Source: restricted.test.ts 'actual pinned Pi child bash path violation is denied and outside bytes stay unchanged'."""
    outside_root = Path(tempfile.mkdtemp(prefix="pi-restricted-tool-outside-", dir="/var/tmp"))  # noqa: S108  # reason: intentionally outside every sandbox mount
    outside = outside_root / "target.txt"
    outside.write_text("PRESERVED\n")
    credential = "restricted-pi-provider-secret-sentinel"  # pragma: allowlist secret
    urls: list[str] = []
    authorization: list[str] = []
    payloads: list[dict[str, Any]] = []
    tool_command = ""

    def respond(handler: BaseHTTPRequestHandler, path: str, body: bytes) -> None:
        urls.append(path)
        authorization.append(handler.headers.get("authorization", ""))
        if path != "/v1/chat/completions":
            handler.send_response(200)
            handler.send_header("content-type", "text/plain")
            handler.end_headers()
            handler.wfile.write(b"unexpected tool network access")
            return
        payload = json.loads(body)
        payloads.append(payload)
        has_result = any(message.get("role") == "tool" for message in payload["messages"])
        sse_headers(handler)
        if has_result:
            sse_write(
                handler,
                chat_chunk({"role": "assistant", "content": "restricted path probe completed"}),
            )
            sse_write(handler, chat_chunk({}, "stop"))
        else:
            call = {
                "index": 0,
                "id": "restricted-write",
                "type": "function",
                "function": {"name": "bash", "arguments": json.dumps({"command": tool_command})},
            }
            sse_write(handler, chat_chunk({"role": "assistant", "tool_calls": [call]}))
            sse_write(handler, chat_chunk({}, "tool_calls"))
        sse_write(handler, chat_usage())
        sse_write(handler, "data: [DONE]\n\n")

    with FixtureServer(respond) as server:
        write_probe = f"require('fs').writeFileSync({json.dumps(str(outside))},'CHANGED\\n')"
        selected_probe = (
            "require('node:fs').writeFileSync(1, 'SELECTED_KEY=' "
            "+ (process.env.RESTRICTED_SELECTED_KEY ?? 'ABSENT') + '\\n')"
        )
        network_probe = (
            f"fetch({json.dumps(f'http://127.0.0.1:{server.port}/tool-network-probe')})"
            ".then(()=>process.exit(0)).catch(()=>{require('node:fs').writeFileSync(1,'NETWORK_DENIED\\n');process.exit(7)})"
        )
        tool_command = "; ".join(
            f"node -e {json.dumps(code)}" for code in (write_probe, selected_probe, network_probe)
        )
        profile = {
            "provider": "fixture",
            "modelId": "fixture-model",
            "endpoint": server.url,
            "api": "openai-completions",
            "apiKeyEnv": "RESTRICTED_SELECTED_KEY",  # pragma: allowlist secret
            "servedContextTokens": 32768,
            "maxCompletionTokens": 4096,
            "resourceGroup": "restricted-tool-fixture",
            "allowProviderFallback": False,
        }
        compiled = compile_profile("restricted-tool", profile, tmp_path / "agent")
        policy = configure_provider_profile(compiled, tmp_path)
        client = RpcClient(
            launch_pi(
                restricted_options(
                    tmp_path, compiled, {**policy.env, "RESTRICTED_SELECTED_KEY": credential}
                )
            )
        )
        try:
            client.send(
                {
                    "type": "prompt",
                    "id": "probe",
                    "message": "attempt the restricted outside path probe and network probe",
                }
            )
            client.until(
                lambda: client.settled_count() >= 1, timeout=HANG_GUARD_SECS, what="agent_settled"
            )
            assert outside.read_text() == "PRESERVED\n"
            assert urls.count("/v1/chat/completions") == 2
            assert "/tool-network-probe" not in urls
            assert authorization.count(f"Bearer {credential}") == 2
            results = [m for p in payloads for m in p["messages"] if m.get("role") == "tool"]
            assert len(results) == 1
            assert results[0]["tool_call_id"] == "restricted-write"
            content = json.dumps(results[0]["content"])
            assert re.search(r"EROFS|read-only|EACCES|EPERM", content, re.IGNORECASE)
            assert "NETWORK_DENIED" in content
            assert "SELECTED_KEY=ABSENT" in content
            assert credential not in content
            events = client.snapshot()
            assert any(
                e.get("type") == "tool_execution_end"
                and e.get("toolCallId") == "restricted-write"
                and e.get("toolName") == "bash"
                and e.get("isError") is True
                for e in events
            )
            assert any(
                e.get("type") == "message_end"
                and "restricted path probe completed" in json.dumps(e.get("message"))
                for e in events
            )
        finally:
            client.close()
            shutil.rmtree(outside_root, ignore_errors=True)
