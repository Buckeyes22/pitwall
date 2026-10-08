"""Hermetic personal-serving journeys J28-J31: no database, fake RunPod, real CLI verbs.

Each journey runs ``pitwall setup``, ``serve``, ``status``, or ``stop`` exactly as the CLI
does, with a temporary home and state root. Only the provider boundary is faked: the
personal engine is built over ``tests.fakes.personal`` in place of live RunPod.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from pitwall.cli import personal as cli_personal
from pitwall.personal.state import StateStore
from tests.fakes import personal as fakes

pytestmark = [pytest.mark.release]

_SERVE = [
    "--model",
    fakes.MODEL,
    "--gpu-class",
    fakes.GPU,
    "--ttl-minutes",
    "15",
    "--max-usd-per-hour",
    "1.00",
]


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("SHELL", "/bin/bash")
    monkeypatch.setenv("RUNPOD_API_KEY", "journey-placeholder")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("PITWALL_ENDPOINT_KEY", raising=False)
    return tmp_path


class _Fakes:
    def __init__(self) -> None:
        self.runpod = fakes.FakeRunPod()
        self.routes = fakes.FakeRoutes()
        self.clock = fakes.FakeClock()


@pytest.fixture
def world(home: Path, monkeypatch: pytest.MonkeyPatch) -> _Fakes:
    state = _Fakes()
    monkeypatch.setattr("pitwall.personal.service.plan_catalogue_model", fakes.fake_plan)
    monkeypatch.setattr(
        cli_personal,
        "_service_or_exit",
        lambda: fakes.build_service(
            home / "state" / "pitwall", state.runpod, state.routes, clock=state.clock
        ),
    )
    return state


def _serve(route: str, *extra: str) -> list[str]:
    return [*_SERVE, "--route", route, *extra]


def test_j28_first_run_setup_is_idempotent_and_owner_only(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli_personal.cmd_setup(["--yes"]) == 0
    key = home / "state" / "pitwall" / "endpoint.key"
    first = key.read_text()
    assert len(first.strip()) >= 32
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    assert stat.S_IMODE(key.parent.stat().st_mode) == 0o700
    profile = home / ".bashrc"
    exports = profile.read_text().count("PITWALL_ENDPOINT_KEY")

    assert cli_personal.cmd_setup(["--yes"]) == 0
    assert key.read_text() == first, "setup must not rotate an existing endpoint key"
    assert profile.read_text().count("PITWALL_ENDPOINT_KEY") == exports, "one profile export"
    assert (
        "Backend: personal (local state file; set [personal] backend in pitwall.toml)"
        in capsys.readouterr().out
    )


@pytest.mark.parametrize(
    ("argv", "code"),
    [
        (_serve("ornith", "--max-usd-per-hour", "0.01"), "price_over_cap"),
        (
            [*_SERVE[:2], "--gpu-class", fakes.SMALL_GPU, *_SERVE[4:], "--route", "small"],
            "does_not_fit",
        ),
        (
            [*_SERVE[:2], "--gpu-class", "NVIDIA RTX A5000", *_SERVE[4:], "--route", "a5k"],
            "unpriced",
        ),
        (_serve("taken"), "route_exists"),
    ],
    ids=["price_over_cap", "does_not_fit", "unpriced", "route_exists"],
)
def test_j29_serve_refusals_create_no_pod(
    world: _Fakes, argv: list[str], code: str, capsys: pytest.CaptureFixture[str]
) -> None:
    world.routes.existing.add("taken")
    assert cli_personal.cmd_serve(argv) == 1
    assert f"refused: {code}" in capsys.readouterr().err
    assert world.runpod.created == []


def test_j29_serve_refuses_a_ttl_the_startup_budget_would_outlast(
    world: _Fakes, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ornith's dossier allows 30 min to start; a 15-minute TTL would expire first."""

    async def thirty_minute_start(request: object, **_kwargs: object) -> object:
        plan = await fakes.fake_plan(request)
        return plan.model_copy(update={"startup_timeout_s": 1800})

    monkeypatch.setattr("pitwall.personal.service.plan_catalogue_model", thirty_minute_start)
    assert cli_personal.cmd_serve(_serve("short")) == 1
    assert "refused: ttl_below_startup" in capsys.readouterr().err
    assert world.runpod.created == []


def test_j29_serve_without_the_routing_cli_creates_no_pod(
    world: _Fakes, capsys: pytest.CaptureFixture[str]
) -> None:
    world.routes.cli_present = False
    assert cli_personal.cmd_serve(_serve("ornith")) == 1
    assert "refused: routing_cli_missing" in capsys.readouterr().err
    assert world.runpod.created == []


def test_j30_serve_status_stop_lifecycle(
    home: Path, world: _Fakes, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli_personal.cmd_serve(_serve("ornith", "--json")) == 0
    served = json.loads(capsys.readouterr().out)
    assert served["route"] == "ornith" and served["state"] == "ready"
    assert served["endpoint_url"] == "https://pod123-8000.proxy.runpod.net/v1"
    assert served["try"] == "route-shim.sh ornith prompt.md"
    [created] = world.runpod.created
    script = created["docker_start_cmd"][0]
    assert "pods/$RUNPOD_POD_ID/action" in script and "sleep 900;" in script
    assert "journey-placeholder" not in json.dumps(served)
    assert fakes.ENDPOINT_KEY not in json.dumps(served)
    assert created["workload"].allowed_cuda_versions == ["12.8", "13.0"]
    assert world.routes.names("attach") == ["ornith"]

    assert cli_personal.cmd_status(["--json"]) == 0
    [row] = json.loads(capsys.readouterr().out)["leases"]
    assert (row["route"], row["state"]) == ("ornith", "ready")

    assert cli_personal.cmd_stop(["ornith", "--json"]) == 0
    capsys.readouterr()
    assert world.runpod.terminated == ["pod123"]
    assert world.routes.names("remove") == ["ornith"]
    record = StateStore(home / "state" / "pitwall").get("ornith")
    assert record is not None and record.state == "stopped"


def test_j31_status_reconciles_gone_and_overdue_pods(
    home: Path, world: _Fakes, capsys: pytest.CaptureFixture[str]
) -> None:
    for route in ("gone", "late"):
        assert cli_personal.cmd_serve(_serve(route, "--json")) == 0
    capsys.readouterr()
    world.runpod.vanish("pod-gone")
    world.clock.advance(minutes=16)

    assert cli_personal.cmd_status(["--json"]) == 0
    states = {
        row["route"]: (row["state"], row.get("failure"))
        for row in json.loads(capsys.readouterr().out)["leases"]
    }
    assert states["gone"][0] == "gone"
    assert states["late"] == ("stopped", "terminated_late")
    assert "pod-late" in world.runpod.terminated


def test_j30_stopping_an_unknown_route_is_a_clean_refusal(
    world: _Fakes, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli_personal.cmd_stop(["no-such-route", "--json"]) == 1
    assert "refused: unknown_route no-such-route" in capsys.readouterr().err
    assert world.runpod.terminated == []


@pytest.mark.parametrize("stage", ["attach", "probe"])
def test_j30_serve_interrupted_after_the_pod_exists_leaves_no_pod_and_says_interrupted(
    home: Path,
    world: _Fakes,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    def interrupt(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(world.routes, stage, interrupt)
    assert cli_personal.cmd_serve(_serve("ornith")) == 130
    assert "interrupted" in capsys.readouterr().err
    assert world.runpod.terminated == ["pod123"]
    record = StateStore(home / "state" / "pitwall").get("ornith")
    assert record is not None
    assert (record.state, record.failure) == ("failed", "interrupted")
