"""Install wheel and sdist outside the checkout and exercise public contracts."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

CHECKOUT = Path(__file__).resolve().parents[2]
SERVICES = (
    "pitwall-api",
    "pitwall-reconciler",
    "pitwall-webhook",
    "pitwall-cost-exporter",
)


def _run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=cwd, env=env, check=True)


def _json(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> Any:
    result = subprocess.run(command, cwd=cwd, env=env, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def _bare_env(root: Path) -> dict[str, str]:
    """No runtime settings: a fresh host that has only installed the artifact."""
    return {"HOME": str(root / "home"), "PATH": "/usr/bin:/bin"}


def _smoke_offline(environment: Path, root: Path) -> None:
    """Service --help, packaged dossiers, catalog lock, and route table, unconfigured."""
    (root / "home").mkdir(exist_ok=True)
    env = _bare_env(root)
    for service in SERVICES:
        usage = subprocess.run(
            [str(environment / "bin" / service), "--help"],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        ).stdout
        if not usage.startswith(f"usage: {service}"):
            raise RuntimeError(f"installed {service} --help printed no usage")
    cli = str(environment / "bin" / "pitwall")
    checkout_cli = str(Path(sys.executable).parent / "pitwall")
    installed_models = _json([cli, "models", "list", "--json"], cwd=root, env=env)
    checkout_models = _json([checkout_cli, "models", "list", "--json"], cwd=CHECKOUT)
    if installed_models != checkout_models or not installed_models["models"]:
        raise RuntimeError("installed model dossiers differ from the checkout's")
    status = _json([cli, "gateway", "status", "--json"], cwd=root, env=env)
    lock = json.loads((CHECKOUT / "config" / "gateway-catalog.lock.json").read_text())
    if status != {"lock": lock, "quotas": None, "source": "local", "api_url": None}:
        raise RuntimeError("installed gateway status does not read the packaged catalog lock")
    routes = subprocess.run(
        [
            str(environment / "bin" / "python"),
            "-c",
            "from pitwall.personal.gateway import routes_path; print(routes_path())",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    packaged = Path(routes)
    if (
        not packaged.is_relative_to(environment)
        or packaged.read_bytes() != (CHECKOUT / "config" / "gateway-routes.json").read_bytes()
    ):
        raise RuntimeError(f"installed route table is not the packaged copy: {routes}")


def _agents_env(root: Path, environment: Path) -> dict[str, str]:
    """An isolated home and routing state, with the installed environment's tools first."""
    home = root / "agents-home"
    values = {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / "config"),
        "XDG_DATA_HOME": str(home / "data"),
        "XDG_STATE_HOME": str(home / "state"),
        "XDG_CACHE_HOME": str(home / "cache"),
        "TMPDIR": str(home / "tmp"),
        "PATH": f"{environment / 'bin'}:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PITWALL_AGENTS_PROFILES": str(root / "routing-config" / "profiles.json"),
        "PITWALL_AGENTS_STATE_HOME": str(root / "routing-state"),
        "PITWALL_AGENTS_LEDGER": str(root / "routing-state" / "observations.jsonl"),
    }
    for directory in (
        home / "config",
        home / "data",
        home / "state",
        home / "cache",
        home / "tmp",
        root / "routing-config",
        root / "routing-state",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return values


def _assert_doctor(payload: dict[str, Any]) -> None:
    checks = payload.get("checks")
    if not isinstance(checks, list) or not checks:
        raise RuntimeError("artifact doctor returned no checks")
    failures = [item for item in checks if isinstance(item, dict) and item.get("status") == "FAIL"]
    if failures:
        raise RuntimeError(f"artifact doctor reported failures: {failures}")


def _smoke_agents(environment: Path, root: Path) -> None:
    """The installed agents runtime: doctor, profiles, a fake-harness workflow, the receiver unit.

    Ends by making the installed package read-only, so it also proves the runtime never writes
    into its own install.
    """
    from pitwall.agents.registry import load_registry

    env = _agents_env(root, environment)
    python = environment / "bin" / "python"
    command = environment / "bin" / "pitwall"
    fake = environment / "bin" / "opencode"
    fake.write_text("#!/bin/sh\nprintf 'pong\\n'\n", encoding="utf-8")
    fake.chmod(0o700)

    def run(*argv: str) -> str:
        return subprocess.run(
            [*argv], cwd=root, env=env, check=True, capture_output=True, text=True
        ).stdout

    version = run(str(command), "--version").strip().rsplit(" ", 1)[-1]
    if run(str(command), "agents", "--version").strip() != f"pitwall agents {version}":
        raise RuntimeError("'pitwall agents --version' does not match 'pitwall --version'")
    harnesses = run(
        str(python),
        "-c",
        "from pitwall.agents.registry import load_registry; "
        "print(len(load_registry()['harnesses']))",
    )
    if int(harnesses) != len(load_registry()["harnesses"]):
        raise RuntimeError("installed harness registry differs from the checkout's")
    _assert_doctor(json.loads(run(str(command), "agents", "doctor", "--json")))
    run(
        str(command),
        "agents",
        "profiles",
        "add",
        "artifact-route",
        "--harness",
        "opencode",
        "--model",
        "fixture/model",
    )
    resolved = json.loads(
        run(str(command), "agents", "profiles", "resolve", "artifact-route", "--json")
    )
    if resolved.get("harness") != "opencode":
        raise RuntimeError("profile resolution did not use the packaged registry")
    workflow = root / "workflow.json"
    workflow.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "name": "artifact-recursion",
                "defaults": {
                    "maxConcurrency": 1,
                    "workspace": "shared",
                    "failurePolicy": "fail-fast",
                },
                "tasks": {
                    "pong": {
                        "route": {"provider": "opencode", "model": "fixture/model"},
                        "mode": "read",
                        "prompt": {"text": "Return exact pong."},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    outcome = json.loads(
        run(str(command), "agents", "workflow", "run", "--host", "codex", str(workflow))
    )
    if outcome.get("status") != "succeeded":
        raise RuntimeError(f"installed workflow run failed: {outcome}")
    run(str(command), "agents", "broker", "receiver", "--install")
    unit = Path(env["HOME"]) / ".config/systemd/user/pitwall-agents-broker.service"
    text = unit.read_text(encoding="utf-8")
    if f"{python} -m pitwall.agents broker receiver" not in text:
        raise RuntimeError(f"receiver unit does not run the installed environment: {text}")
    if str(CHECKOUT) in text:
        raise RuntimeError("receiver unit contains a checkout path")
    package = Path(run(str(python), "-c", "import pitwall.agents as a; print(a.__file__)").strip())
    for path in sorted(package.parent.rglob("*"), reverse=True):
        path.chmod(path.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    package.parent.chmod(
        package.parent.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    )
    run(str(command), "agents", "--version")
    run(str(python), "-c", "from pitwall.agents.registry import load_registry; load_registry()")


def _smoke_mcp(environment: Path, root: Path, child_env: dict[str, str]) -> None:
    """Initialize the installed stdio server and list the same tools as the checkout."""
    import asyncio

    from pitwall.mcp import mcp

    expected = sorted(tool.name for tool in asyncio.run(mcp.list_tools()))
    messages: list[dict[str, Any]] = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "artifact-smoke", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    process = subprocess.Popen(
        [str(environment / "bin" / "pitwall"), "mcp", "serve", "broker"],
        cwd=root,
        env=child_env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    assert process.stdin is not None and process.stdout is not None
    try:
        responses: dict[int, Any] = {}
        for message in messages:
            process.stdin.write(json.dumps(message) + "\n")
            process.stdin.flush()
            if "id" in message:
                reply = json.loads(process.stdout.readline())
                responses[int(reply["id"])] = reply
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    server = responses[1]["result"]["serverInfo"]
    if server["name"] != "pitwall":
        raise RuntimeError(f"installed MCP server identified as {server!r}")
    tools = sorted(tool["name"] for tool in responses[2]["result"]["tools"])
    if tools != expected:
        raise RuntimeError("installed MCP server lists different tools than the checkout")


def _free_loopback_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _request_json(
    url: str,
    *,
    token: str,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    body = None if payload is None else json.dumps(payload).encode()
    headers = {"Authorization": f"Bearer {token}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(request, timeout=3) as response:
        if response.status != 200:
            raise RuntimeError(f"artifact API smoke returned HTTP {response.status}")
        value = json.load(response)
    if not isinstance(value, dict):
        raise RuntimeError("artifact API smoke returned a non-object response")
    return value


def _smoke_api(environment: Path, root: Path, child_env: dict[str, str]) -> None:
    port = _free_loopback_port()
    token = "artifact-smoke-api-token-0001"
    api_env = child_env | {
        "PITWALL_API_HOST": "127.0.0.1",
        "PITWALL_API_PORT": str(port),
        "PITWALL_API_TOKEN": token,
        "PITWALL_ADMIN_SECRET": "artifact-smoke-admin-secret-0001",
    }
    log_path = root / "api.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [str(environment / "bin" / "pitwall-api")],
            cwd=root,
            env=api_env,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            ready: dict[str, object] | None = None
            for _ in range(60):
                if process.poll() is not None:
                    raise RuntimeError(f"installed API exited early; see {log_path}")
                try:
                    ready = _request_json(f"http://127.0.0.1:{port}/readyz", token=token)
                    break
                except OSError, urllib.error.URLError:
                    time.sleep(0.25)
            if ready is None or ready.get("ok") is not True:
                raise RuntimeError("installed API did not become ready")
            capabilities = _request_json(f"http://127.0.0.1:{port}/v1/capabilities", token=token)
            if "items" not in capabilities:
                raise RuntimeError("installed API capability response is malformed")
            inference = _request_json(
                f"http://127.0.0.1:{port}/v1/inference",
                token=token,
                payload={"capability": "embedding.demo", "texts": ["hello"], "dry_run": True},
            )
            result = inference.get("result")
            if not isinstance(result, dict) or result.get("dry_run") is not True:
                raise RuntimeError("installed API dry-run request did not remain dry-run")
        finally:
            # SIGINT follows Uvicorn's graceful-shutdown path and therefore
            # verifies that application lifespan cleanup completes cleanly.
            process.send_signal(signal.SIGINT)
            try:
                return_code = process.wait(timeout=10)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                process.wait(timeout=5)
                raise RuntimeError("installed API did not stop within 10 seconds") from exc
            if return_code != 0:
                raise RuntimeError(f"installed API shutdown returned {return_code}; see {log_path}")


def smoke(artifact: Path) -> None:
    uv = os.environ.get("UV", "uv")
    with tempfile.TemporaryDirectory(prefix="pitwall-artifact-smoke-") as temp:
        root = Path(temp)
        environment = root / "venv"
        _run([uv, "venv", str(environment)], cwd=root)
        python = environment / "bin" / "python"
        _run([uv, "pip", "install", "--python", str(python), str(artifact.resolve())], cwd=root)
        cli = environment / "bin" / "pitwall"
        _run([str(cli), "--help"], cwd=root)
        _run([str(cli), "--version"], cwd=root)
        _smoke_offline(environment, root)
        _run(
            [
                str(python),
                "-c",
                (
                    "import pitwall; from pitwall.migrations import discover_migrations; "
                    "items=discover_migrations(); assert len(items) >= 20; "
                    "assert all(item.sql for item in items); print(pitwall.__version__)"
                ),
            ],
            cwd=root,
        )
        database_url = os.environ.get("PITWALL_TEST_DATABASE_URL")
        if database_url:
            child_env = os.environ.copy()
            child_env["DATABASE_URL"] = database_url
            child_env["REDIS_URL"] = os.environ.get(
                "PITWALL_TEST_REDIS_URL", "redis://127.0.0.1:6380/0"
            )
            child_env["RUNPOD_API_KEY"] = "artifact-smoke-runpod-key"
            _run([str(cli), "db", "migrate"], cwd=root, env=child_env)
            _run([str(cli), "db", "status"], cwd=root, env=child_env)
            _run([str(cli), "config", "check"], cwd=root, env=child_env)
            _run([str(cli), "init", "--non-interactive", "--json"], cwd=root, env=child_env)
            _smoke_api(environment, root, child_env)
            _smoke_mcp(environment, root, child_env)
        _smoke_agents(environment, root)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    artifacts = sorted(args.directory.glob("*.whl")) + sorted(args.directory.glob("*.tar.gz"))
    if len(artifacts) != 2:
        parser.error("exactly one wheel and one sdist are required")
    for artifact in artifacts:
        smoke(artifact)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
