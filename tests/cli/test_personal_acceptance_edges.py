"""Acceptance oracles for personal CLI edges: credential, setup, serve lifecycle, status/stop, spec validation."""

from __future__ import annotations

import datetime as dt
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from pitwall import cli
from pitwall.cli import personal as cli_personal
from pitwall.personal import gateway as gateway_module
from pitwall.personal.keys import ENDPOINT_KEY_ENV
from pitwall.personal.service import ServeFailed, ServeRefused, ServeSpec
from pitwall.personal.state import PersonalLease


def _lease(
    route: str = "ornith",
    *,
    state: str = "ready",
    pod_id: str = "pod-1",
    served_model_id: str = "ornith-model",
    gpu_class: str = "g",
) -> PersonalLease:
    launched = dt.datetime(2026, 9, 2, tzinfo=dt.UTC)
    return PersonalLease(
        route=route,
        pod_id=pod_id,
        model="m",
        served_model_id=served_model_id,
        engine="llama.cpp",
        variant=None,
        image="example/image",
        gpu_class=gpu_class,
        gpu_count=1,
        cloud="community",
        price_per_hour_usd=str(Decimal("1")),
        endpoint_url=f"https://{pod_id}-8000.proxy.runpod.net/v1",
        key_env=ENDPOINT_KEY_ENV,
        launched_at=launched,
        deadline_at=launched + dt.timedelta(minutes=45),
        state=state,  # type: ignore[arg-type]  # reason: helper takes a plain str for the Literal field
    )


class _FakeService:
    def __init__(
        self,
        *,
        leases: list[PersonalLease] | None = None,
        serve_error: BaseException | None = None,
    ) -> None:
        self.leases = [_lease()] if leases is None else leases
        self.serve_error = serve_error
        self.specs: list[ServeSpec] = []
        self.stopped: list[str] = []
        self.gateways: list[Any] = []

    def with_gateway(self, gateway: Any) -> None:
        self.gateways.append(gateway)

    async def serve(self, spec: ServeSpec, *, progress: Any = None) -> PersonalLease:
        del progress
        self.specs.append(spec)
        if self.serve_error is not None:
            raise self.serve_error
        return self.leases[0]

    async def status(self) -> list[PersonalLease]:
        return list(self.leases)

    async def stop(self, route: str) -> PersonalLease:
        self.stopped.append(route)
        for lease in self.leases:
            if lease.route == route:
                return lease
        return _lease(route)


class _FakeSupervisor:
    instances: list[_FakeSupervisor] = []

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self.started = 0
        self.stopped = 0
        _FakeSupervisor.instances.append(self)

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1


@pytest.fixture
def fake_gateway(monkeypatch: pytest.MonkeyPatch) -> type[_FakeSupervisor]:
    _FakeSupervisor.instances = []
    monkeypatch.setattr(gateway_module, "GatewaySupervisor", _FakeSupervisor)
    return _FakeSupervisor


def _personal_backend(
    monkeypatch: pytest.MonkeyPatch, service: _FakeService, *, key: str | None = "k"
) -> list[Any]:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    if key is None:
        monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
        monkeypatch.setenv("HOME", "/nonexistent-pitwall-home")
    else:
        monkeypatch.setenv("RUNPOD_API_KEY", key)
    built: list[Any] = []

    def fake_build(settings: Any) -> _FakeService:
        del settings
        built.append(service)
        return service

    monkeypatch.setattr(cli_personal, "_build_service", fake_build)
    return built


def _serve_argv(*extra: str) -> list[str]:
    return [
        "serve",
        "--model",
        "m",
        "--gpu-class",
        "g",
        "--route",
        "ornith",
        "--max-usd-per-hour",
        "1",
        *extra,
    ]


def test_missing_noninteractive_credential_exits_2_before_service_construction(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    built = _personal_backend(monkeypatch, _FakeService(), key=None)
    monkeypatch.setattr(
        cli_personal,
        "run_personal_setup",
        MagicMock(side_effect=AssertionError("setup must not run")),
    )

    with pytest.raises(SystemExit) as caught:
        cli.main(["status", "--json"])
    assert caught.value.code == 2
    assert built == []
    err = capsys.readouterr().err
    assert "no RunPod credential: run `pitwall setup` or export RUNPOD_API_KEY" in err
    assert "Traceback" not in err


def test_serve_missing_noninteractive_credential_exits_2_before_service_construction(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    built = _personal_backend(monkeypatch, _FakeService(), key=None)
    monkeypatch.setattr(
        cli_personal,
        "run_personal_setup",
        MagicMock(side_effect=AssertionError("setup must not run")),
    )

    with pytest.raises(SystemExit) as caught:
        cli.main(_serve_argv("--json"))
    assert caught.value.code == 2
    assert built == []
    captured = capsys.readouterr()
    assert "no RunPod credential" in captured.err
    assert captured.out == ""


def test_setup_yes_forwards_an_always_yes_prompt(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from pitwall.personal import setup as setup_module
    from pitwall.personal.state import default_state_root

    seen: dict[str, Any] = {}

    def fake_run_setup(**kwargs: Any) -> None:
        seen.update(kwargs)

    monkeypatch.setattr(setup_module, "run_setup", fake_run_setup)
    monkeypatch.setattr(
        cli_personal,
        "run_personal_setup",
        MagicMock(side_effect=AssertionError("interactive path")),
    )

    assert cli_personal.cmd_setup(["--yes"]) == 0
    assert seen["environ"] is cli_personal.os.environ
    assert seen["home"] == Path.home()
    assert seen["state_root"] == default_state_root()
    assert seen["prompt"]("anything at all?") is True
    assert capsys.readouterr().out == ""


def test_setup_without_yes_uses_the_home_state_root_and_interactive_prompt(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from pitwall.personal import setup as setup_module
    from pitwall.personal.state import default_state_root

    seen: dict[str, Any] = {}

    def fake_run_setup(**kwargs: Any) -> None:
        seen.update(kwargs)

    monkeypatch.setattr(setup_module, "run_setup", fake_run_setup)
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")

    assert cli_personal.cmd_setup([]) == 0
    assert seen["environ"] is cli_personal.os.environ
    assert seen["state_root"] == default_state_root()
    assert seen["prompt"]("Update shell profile?") is True
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("rc", [1, 3, 130])
def test_serve_refused_failed_interrupted_exit_codes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], rc: int
) -> None:
    if rc == 1:
        error: BaseException = ServeRefused("price_over_cap", "3 > 1")
        expected = "refused: price_over_cap 3 > 1"
    elif rc == 3:
        error = ServeFailed("route_attach_failed", "pod-9")
        expected = "failed after launch: route_attach_failed; termination attempted for pod pod-9"
    else:
        error = KeyboardInterrupt()
        expected = "interrupted; cleanup was attempted for any pod created"
    service = _FakeService(serve_error=error)
    _personal_backend(monkeypatch, service)

    assert cli.main(_serve_argv("--json")) == rc
    captured = capsys.readouterr()
    assert captured.out == ""
    assert expected in captured.err
    assert "Traceback" not in captured.err
    assert service.specs[0].route == "ornith"


def test_serve_plain_output_has_no_secret_material(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "super-secret-endpoint-key"
    monkeypatch.setenv(ENDPOINT_KEY_ENV, secret)
    _personal_backend(monkeypatch, _FakeService())

    assert cli.main(_serve_argv("--json")) == 0
    assert secret not in capsys.readouterr().out

    assert cli.main(_serve_argv()) == 0
    assert secret not in capsys.readouterr().out


def test_status_personal_empty_human_and_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(ENDPOINT_KEY_ENV, raising=False)
    _personal_backend(monkeypatch, _FakeService(leases=[]))

    assert cli.main(["status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"backend": "personal", "leases": []}

    assert cli.main(["status"]) == 0
    captured = capsys.readouterr()
    assert (
        captured.out
        == "Backend: personal (local state file; set [personal] backend in pitwall.toml)"
        + "\nnothing running\n"
    )
    assert captured.err == ""


def test_status_personal_human_rows_and_endpoint_key_warning(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    lease = _lease(gpu_class="NVIDIA GeForce RTX 4090", served_model_id="ornith-8b")
    monkeypatch.delenv(ENDPOINT_KEY_ENV, raising=False)
    _personal_backend(monkeypatch, _FakeService(leases=[lease]))

    assert cli.main(["status"]) == 0
    captured = capsys.readouterr()
    assert captured.out.splitlines()[0] == (
        "Backend: personal (local state file; set [personal] backend in pitwall.toml)"
    )
    assert captured.out.splitlines()[1] == (
        "ornith               ready      ornith-8b                    "
        "NVIDIA GeForce RTX 4090      ends 2026-09-02T00:45:00+00:00"
    )
    assert (
        f"note: {ENDPOINT_KEY_ENV} is not set in this shell; routes will not "
        "authenticate until it is" in captured.out
    )
    assert captured.err == ""

    monkeypatch.setenv(ENDPOINT_KEY_ENV, "shell-key")
    assert cli.main(["status"]) == 0
    captured = capsys.readouterr()
    assert captured.out.splitlines()[1].startswith("ornith")
    assert ENDPOINT_KEY_ENV not in captured.out


def test_status_personal_json_shape(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(ENDPOINT_KEY_ENV, "shell-key")
    _personal_backend(monkeypatch, _FakeService(leases=[_lease()]))

    assert cli.main(["status", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert list(payload) == ["backend", "leases"]
    assert payload["backend"] == "personal"
    [row] = payload["leases"]
    assert row["route"] == "ornith"
    assert row["state"] == "ready"
    assert row["served_model_id"] == "ornith-model"
    assert row["deadline_at"] == "2026-09-02T00:45:00Z"


def test_status_setup_prompt_when_stdin_is_a_tty(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    monkeypatch.setenv("HOME", "/nonexistent-pitwall-home")
    built = _personal_backend(monkeypatch, _FakeService(leases=[]), key=None)
    calls: list[str] = []
    monkeypatch.setattr(cli_personal, "run_personal_setup", lambda: calls.append("setup"))

    with pytest.raises(SystemExit) as caught:
        cli.main(["status"])
    assert caught.value.code == 2
    assert calls == ["setup"]
    assert built == []
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no RunPod credential" in captured.err


def test_stop_all_selects_only_ready_and_launching_routes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fake_gateway: type[_FakeSupervisor],
) -> None:
    service = _FakeService(
        leases=[
            _lease("ready-route", state="ready"),
            _lease("launching-route", state="launching", pod_id="pod-2"),
            _lease("stopped-route", state="stopped", pod_id="pod-3"),
            _lease("failed-route", state="failed", pod_id="pod-4"),
            _lease("gone-route", state="gone", pod_id="pod-5"),
        ]
    )
    _personal_backend(monkeypatch, service)

    assert cli.main(["stop", "--all", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [row["route"] for row in payload["stopped"]] == ["ready-route", "launching-route"]
    assert service.stopped == ["ready-route", "launching-route"]
    assert payload["gateway_stopped"] is True
    assert [supervisor.stopped for supervisor in fake_gateway.instances] == [1]


def test_stop_all_human_output_and_empty_selection(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fake_gateway: type[_FakeSupervisor],
) -> None:
    service = _FakeService(leases=[_lease("ready-route", state="ready")])
    _personal_backend(monkeypatch, service)

    assert cli.main(["stop", "--all"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "stopped ready-route"
    assert "stopped gateway" in out

    _FakeSupervisor.instances = []
    empty = _FakeService(leases=[_lease("stopped-route", state="stopped")])
    _personal_backend(monkeypatch, empty)
    assert cli.main(["stop", "--all", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["stopped"] == []
    assert empty.stopped == []
    assert payload["gateway_stopped"] is True
    del fake_gateway


def test_stop_named_route_does_not_touch_the_gateway(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fake_gateway: type[_FakeSupervisor],
) -> None:
    service = _FakeService(leases=[_lease("ornith")])
    _personal_backend(monkeypatch, service)

    assert cli.main(["stop", "ornith", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["stopped"][0]["route"] == "ornith"
    assert service.stopped == ["ornith"]
    assert _FakeSupervisor.instances == []


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--gpu-count", "0"], "gpu_count"),
        (["--ttl", "0"], "ttl_minutes"),
        (["--route", "9bad"], "route"),
        (["--max-usd-per-hour", "0"], "max_usd_per_hour"),
        (["--max-usd-per-hour", "-1"], "max_usd_per_hour"),
        (["--max-usd-per-hour", "nan"], "max_usd_per_hour"),
        (["--max-usd-per-hour", "inf"], "max_usd_per_hour"),
    ],
)
def test_serve_invalid_spec_exits_2_before_gateway_and_service(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fake_gateway: type[_FakeSupervisor],
    extra: list[str],
    message: str,
) -> None:
    service = _FakeService()
    built = _personal_backend(monkeypatch, service)
    monkeypatch.setenv("PITWALL_GATEWAY_TOKEN", "tok")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    assert cli.main([*_serve_argv("--gateway", "--json"), *extra]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert message in captured.err
    assert "Traceback" not in captured.err
    assert "invalid" in captured.err or "must" in captured.err
    assert fake_gateway.instances == []
    assert built == []
    assert service.specs == []


def test_serve_invalid_spec_error_is_actionable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fake_gateway: type[_FakeSupervisor],
) -> None:
    _personal_backend(monkeypatch, _FakeService())
    monkeypatch.setenv("PITWALL_GATEWAY_TOKEN", "tok")

    assert cli.main([*_serve_argv("--gateway", "--ttl", "0")]) == 2
    err = capsys.readouterr().err
    assert "ttl_minutes" in err
    assert "greater than or equal to 5" in err
    assert fake_gateway.instances == []


def test_missing_credentials_never_starts_requested_gateway(monkeypatch, fake_gateway):
    monkeypatch.setattr(cli_personal, "select_backend", lambda: "personal")
    monkeypatch.setattr(cli_personal, "_service_or_exit", MagicMock(side_effect=SystemExit(2)))
    with pytest.raises(SystemExit) as caught:
        cli.main(_serve_argv("--gateway"))
    assert caught.value.code == 2
    assert fake_gateway.instances == []


def test_service_build_without_a_key_exits_2_with_the_credential_message(monkeypatch, capsys):
    from pitwall.personal.service import RunPodCredentialMissing
    from pitwall.runpod_credentials import MISSING_CREDENTIAL_MESSAGE

    monkeypatch.setenv("RUNPOD_API_KEY", "fixture-key")

    def refuse(settings):
        raise RunPodCredentialMissing

    monkeypatch.setattr(cli_personal, "_build_service", refuse)
    with pytest.raises(SystemExit) as caught:
        cli_personal._service_or_exit()
    assert caught.value.code == 2
    assert MISSING_CREDENTIAL_MESSAGE in capsys.readouterr().err


@pytest.mark.parametrize("existing", ["", "   "])
def test_runpodctl_fallback_replaces_unusable_environment_key(monkeypatch, existing):
    monkeypatch.setenv("RUNPOD_API_KEY", existing)
    monkeypatch.setattr(
        "pitwall.personal.keys.resolve_runpod_api_key",
        lambda environ: ("fixture-resolved-key", "runpodctl"),
    )
    observed = []
    monkeypatch.setattr(
        cli_personal,
        "_build_service",
        lambda settings: observed.append(cli_personal.os.environ["RUNPOD_API_KEY"]),
    )
    cli_personal._service_or_exit()
    assert observed == ["fixture-resolved-key"]


def test_serve_refusal_does_not_start_optional_gateway(monkeypatch, fake_gateway, capsys):
    service = _FakeService(serve_error=ServeRefused("price_over_cap", "3 > 1"))
    _personal_backend(monkeypatch, service)
    assert cli.main(_serve_argv("--gateway")) == 1
    assert "price_over_cap" in capsys.readouterr().err
    assert len(service.gateways) == 1
    assert [item.started for item in fake_gateway.instances] == [0]


def test_gateway_start_refusal_keeps_documented_exit_2(monkeypatch, capsys, fake_gateway):
    _personal_backend(
        monkeypatch,
        _FakeService(serve_error=ServeRefused("gateway_start_failed", "check gateway setup")),
    )
    assert cli.main(_serve_argv("--gateway")) == 2
    captured = capsys.readouterr()
    assert "gateway_start_failed" in captured.err and "Traceback" not in captured.err


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        (
            [],
            {
                "model": "m",
                "variant": None,
                "gpu_class": "g",
                "gpu_count": 1,
                "cloud": "community",
                "ttl_minutes": 60,
                "max_usd_per_hour": Decimal("1"),
                "rate_per_second": None,
                "route": "ornith",
            },
        ),
        (
            [
                "--model",
                "org/model",
                "--variant",
                "Q4_K_M",
                "--gpu-class",
                "NVIDIA L4",
                "--gpu-count",
                "2",
                "--cloud",
                "secure",
                "--ttl-minutes",
                "90",
                "--max-usd-per-hour",
                "1.50",
                "--rate-per-second",
                "0.002",
                "--route",
                "alternate",
            ],
            {
                "model": "org/model",
                "variant": "Q4_K_M",
                "gpu_class": "NVIDIA L4",
                "gpu_count": 2,
                "cloud": "secure",
                "ttl_minutes": 90,
                "max_usd_per_hour": Decimal("1.50"),
                "rate_per_second": Decimal("0.002"),
                "route": "alternate",
            },
        ),
    ],
)
def test_serve_cli_forwards_every_documented_personal_option(monkeypatch, capsys, extra, expected):
    service = _FakeService()
    _personal_backend(monkeypatch, service)
    assert cli.main([*_serve_argv("--json"), *extra]) == 0
    assert len(service.specs) == 1
    spec = service.specs[0]
    assert {key: getattr(spec, key) for key in expected} == expected
    assert json.loads(capsys.readouterr().out)["route"] == "ornith"


def test_serve_failure_prints_the_pods_log_tail(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The pod is terminated by the time the operator reads this; its logs are the only cause."""
    error = ServeFailed(
        "container_restarting", "pod-9", log_tail="ggml_cuda_init: failed to initialize CUDA"
    )
    _personal_backend(monkeypatch, _FakeService(serve_error=error))

    assert cli.main(_serve_argv("--json")) == 3
    err = capsys.readouterr().err
    assert "failed after launch: container_restarting" in err
    assert "pod log tail:" in err
    assert "ggml_cuda_init: failed to initialize CUDA" in err
