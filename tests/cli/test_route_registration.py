from __future__ import annotations

import json
import os
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from pitwall.api.capability_schemas import CapabilityResponse
from pitwall.cli import serve_model as cli_serve_model
from tests.agents.profiles_fixture import read_profiles

COMPONENT_ROOT = Path(__file__).parents[2]


class _CapabilityServer:
    def __init__(self) -> None:
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                server.paths.append(self.path)
                body = json.dumps(
                    CapabilityResponse.model_validate(
                        {
                            "id": "cap-glimmer",
                            "name": "llm.glimmer",
                            "version": "1",
                            "class": "llm",
                            "cost_mode": "zero",
                            "served_model_id": "org/model",
                            "active_lease": {
                                "lease_id": "lease-route-1",
                                "expires_at": "2026-08-28T14:00:00+00:00",
                            },
                            "created_at": "2026-08-28T13:00:00+00:00",
                            "updated_at": "2026-08-28T13:00:00+00:00",
                        }
                    ).model_dump(mode="json", by_alias=True)
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args: object) -> None:
                return None

        self.paths: list[str] = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self) -> _CapabilityServer:
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.server.shutdown()
        self.server.server_close()


def _routing_executable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    executable = tmp_path / "fake-routing"
    log = tmp_path / "routing-calls.jsonl"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import os
import shutil
import sys
from pathlib import Path

with Path(os.environ["ROUTING_CALL_LOG"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\\n")
mode = os.environ.get("ROUTING_FAKE_MODE", "success")
if mode == "exists" and sys.argv[1:3] == ["profiles", "add"]:
    print(
        "already registered from Pitwall; run `pitwall agents profiles refresh demo`",
        file=sys.stderr,
    )
    raise SystemExit(4)
if mode == "failure":
    print("routing receiver unavailable", file=sys.stderr)
    raise SystemExit(9)
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("ROUTING_CALL_LOG", str(log))
    return executable, log


def _calls(log: Path) -> list[list[str]]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def test_register_route_adds_with_token_only_in_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _executable, log = _routing_executable(tmp_path, monkeypatch)
    env = {**os.environ, "PITWALL_API_TOKEN": "routing-test-token"}

    result = cli_serve_model.register_route(
        routing_cli="fake-routing",
        route="glimmer",
        capability="llm.glimmer",
        base_url="http://127.0.0.1:8080",
        env=env,
    )

    assert result == cli_serve_model.RouteRegistration(ok=True, action="added", stderr="")
    assert _calls(log) == [
        [
            "profiles",
            "add",
            "glimmer",
            "--from-pitwall",
            "llm.glimmer",
            "--pitwall-url",
            "http://127.0.0.1:8080",
        ]
    ]
    assert "routing-test-token" not in log.read_text(encoding="utf-8")


def test_register_route_refreshes_an_existing_pitwall_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _executable, log = _routing_executable(tmp_path, monkeypatch)
    monkeypatch.setenv("ROUTING_FAKE_MODE", "exists")

    result = cli_serve_model.register_route(
        routing_cli="fake-routing",
        route="glimmer",
        capability="llm.glimmer",
        base_url="http://127.0.0.1:8080",
        env=os.environ,
    )

    assert result == cli_serve_model.RouteRegistration(ok=True, action="refreshed", stderr="")
    assert _calls(log)[1] == [
        "profiles",
        "refresh",
        "glimmer",
        "--pitwall-url",
        "http://127.0.0.1:8080",
    ]


def test_register_route_reports_failure_without_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _routing_executable(tmp_path, monkeypatch)
    monkeypatch.setenv("ROUTING_FAKE_MODE", "failure")

    result = cli_serve_model.register_route(
        routing_cli="fake-routing",
        route="glimmer",
        capability="llm.glimmer",
        base_url="http://127.0.0.1:8080",
        env=os.environ,
    )

    assert result == cli_serve_model.RouteRegistration(
        ok=False,
        action="failed",
        stderr="routing receiver unavailable",
    )


def test_serve_model_route_failure_exits_three_with_manual_remedy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _routing_executable(tmp_path, monkeypatch)
    monkeypatch.setenv("ROUTING_FAKE_MODE", "failure")
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    monkeypatch.setattr(cli_serve_model, "_serve_model_async", cli_serve_model._serve_model_async)
    monkeypatch.setattr("pitwall.db.get_pool", AsyncMock(return_value=object()))

    from pitwall.config import PitwallSettings
    from pitwall.serve import ServeResult

    async def fake_serve(*args: object, **kwargs: object) -> ServeResult:
        return ServeResult(
            capability="llm.glimmer",
            lease_id="lease-route-1",
            expires_at="2026-08-28T14:00:00+00:00",
            model_id="org/model",
            proxy_base_url="http://127.0.0.1:8080/v1/openai/llm.glimmer/v1",
            engine="vllm",
            variant=None,
            gpu_count=1,
            workload_id="wkl-route-1",
            template_id="template-route-1",
            provider_id="prov-route-1",
            dry_run=False,
            created=True,
            cost_estimate_usd="0.50",
        )

    monkeypatch.setattr(
        "pitwall.config.get_settings",
        lambda: PitwallSettings(pitwall_routing_cli="fake-routing"),
    )
    monkeypatch.setattr("pitwall.models.load_catalogue", object)
    monkeypatch.setattr("pitwall.serve.serve_model", fake_serve)
    rc = cli_serve_model.cmd_serve_model(
        [
            "--capability",
            "llm.glimmer",
            "--model",
            "org/model",
            "--gpu-class",
            "NVIDIA L4",
            "--image",
            "example/model:test",
            "--route",
            "glimmer",
        ]
    )

    captured = capsys.readouterr()
    assert rc == 3
    assert "serve ok; route registration failed: routing receiver unavailable" in captured.err
    assert (
        "remedy: fake-routing profiles add glimmer --from-pitwall llm.glimmer "
        "--pitwall-url http://127.0.0.1:8080" in captured.err
    )


def test_serve_model_default_bare_command_uses_installed_pitwall_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home with spaces"
    scripts = home / ".claude" / "scripts"
    user_bin = home / ".local" / "bin"
    config_home = home / ".config"
    state_home = home / ".local" / "state"
    cache_home = home / ".cache"
    python_bin = Path(sys.executable).parent
    system_path = f"{python_bin}:/usr/local/bin:/usr/bin:/bin"
    install_env = {
        "HOME": str(home),
        "PATH": system_path,
        "PITWALL_AGENTS_SCRIPTS_DIR": str(scripts),
        "PITWALL_AGENTS_BIN_DIR": str(user_bin),
        "XDG_CONFIG_HOME": str(config_home),
        "XDG_STATE_HOME": str(state_home),
        "XDG_CACHE_HOME": str(cache_home),
    }
    # No routing executable is staged anywhere: the default `pitwall agents` must work alone.
    user_bin.mkdir(parents=True)
    assert shutil.which("pitwall-agent-routing", path=f"{user_bin}:{system_path}") is None

    for key, value in install_env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PATH", f"{user_bin}:{system_path}")
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    monkeypatch.setenv("PITWALL_AGENTS_API_TOKEN", "routing-test-token")
    monkeypatch.delenv("PITWALL_ROUTING_CLI", raising=False)
    monkeypatch.setattr(cli_serve_model, "_serve_model_async", cli_serve_model._serve_model_async)
    monkeypatch.setattr("pitwall.db.get_pool", AsyncMock(return_value=object()))

    from pitwall.config import PitwallSettings
    from pitwall.serve import ServeResult

    async def fake_serve(*args: object, **kwargs: object) -> ServeResult:
        return ServeResult(
            capability="llm.glimmer",
            lease_id="lease-route-1",
            expires_at="2026-08-28T14:00:00+00:00",
            model_id="org/model",
            proxy_base_url="unused",
            engine="vllm",
            variant=None,
            gpu_count=1,
            workload_id="wkl-route-1",
            template_id="template-route-1",
            provider_id="prov-route-1",
            dry_run=False,
            created=True,
            cost_estimate_usd="0.50",
        )

    with _CapabilityServer() as server:
        monkeypatch.setattr(
            "pitwall.config.get_settings",
            lambda: PitwallSettings(pitwall_base_url=server.base_url),
        )
        monkeypatch.setattr("pitwall.models.load_catalogue", object)
        monkeypatch.setattr("pitwall.serve.serve_model", fake_serve)
        rc = cli_serve_model.cmd_serve_model(
            [
                "--capability",
                "llm.glimmer",
                "--model",
                "org/model",
                "--gpu-class",
                "NVIDIA L4",
                "--image",
                "example/model:test",
                "--route",
                "glimmer",
            ]
        )

    assert rc == 0
    assert server.paths == ["/v1/capabilities/llm.glimmer"]
    route = read_profiles(config_home / "pitwall" / "pitwall.toml")["models"]["glimmer"]
    assert route["origin"]["kind"] == "pitwall"
    assert route["origin"]["capability"] == "llm.glimmer"
    assert route["endpoint"]["apiKeyEnv"] == "PITWALL_AGENTS_API_TOKEN"  # pragma: allowlist secret
