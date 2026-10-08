"""J36: every CLI command path and every argument surface, through the real entry points.

``tests.release.cli_explorer`` walks every parser of ``pitwall``, including its ``agents`` and
``usage`` groups (in a subprocess), and records where each argparse surface was registered. Every surface that release-acceptance discovery found (``cli_arguments``) must
be registered at exactly its source line, on a parser whose ``--help`` names it.

Every explored command path has a fixture. Its representative argv runs through the
installed entry point in a throwaway ``HOME`` and working directory, with a minimal ``PATH``
(no harness CLI can launch), placeholder credentials, and outbound HTTP refused by a dead
loopback proxy; commands that need the registry get the seeded disposable database. Each
exits as pinned and, for ``--json``, prints the pinned top-level keys. Destructive commands
run their refusal path and assert the refusal. Long-running commands must still be alive
after a few seconds. An unknown flag exits with usage (2, or the documented 1 for the
hand-written ``pitwall`` dispatcher).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.release, pytest.mark.integration, pytest.mark.journey_harness]

ROOT = Path(__file__).resolve().parents[2]
FIXTURES: dict[str, dict[str, Any]] = json.loads(
    (Path(__file__).parent / "cli_fixtures.json").read_text()
)
BINARIES = {"pitwall": ROOT / ".venv" / "bin" / "pitwall"}
_INTERNAL = {"agents _shim", "agents _steer-gate"}  # add_help=False plumbing, not operators
_DAEMON_WINDOW_S = 6
_ONBOARDING_REQUEST = {
    "name": "j36-onboarding",
    "capability_name": "embedding.demo",
    "capability_class": "embedding",
    "provider_name": "j36-runpod",
    "image": "docker.io/example/worker:sha-abc",
    "public_image": True,
    "gpu_type_ids": ["NVIDIA L4"],
    "rate_per_hour_usd": "0.50",
    "template": {"mode": "create", "name": "j36-template"},
    "endpoint": {"mode": "create", "name": "j36-endpoint"},
    "probe_payload": {"texts": ["hello"]},
}


@dataclass
class Outcome:
    code: int | None
    alive: bool
    stdout: str
    stderr: str
    then_code: int | None
    unknown_code: int
    unknown_stderr: str


def _environment(
    journey_env: dict[str, str], home: Path, *, database: bool, shims: Path
) -> dict[str, str]:
    env = {
        key: value
        for key, value in journey_env.items()
        if key not in {"DATABASE_URL", "REDIS_URL", "PITWALL_MCP_TRANSPORT"}
    }
    env.update(
        {
            "PATH": os.pathsep.join(
                [str(shims), str(BINARIES["pitwall"].parent), "/usr/bin", "/bin"]
            ),
            "HOME": str(home),
            "XDG_STATE_HOME": str(home / ".local" / "state"),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "PITWALL_ADMIN_SECRET": "journey-admin-secret",
            "J36_REGISTRY_PASSWORD": "journey-registry-placeholder",
            "TERM": "dumb",
            "LANG": "C.UTF-8",
        }
    )
    if database:
        env["DATABASE_URL"] = journey_env["DATABASE_URL"]
        env["REDIS_URL"] = journey_env["REDIS_URL"]
    return env


def _argv(program: str, args: list[str]) -> list[str]:
    return [str(BINARIES[program]), *(arg.replace("{repo}", str(ROOT)) for arg in args)]


def _run_one(key: str, journey_env: dict[str, str]) -> Outcome:
    program = key.split("|", 1)[0]
    fixture = FIXTURES[key]
    with tempfile.TemporaryDirectory(prefix="j36-") as tmp:
        work = Path(tmp)
        home = work / "home"
        home.mkdir()
        (work / "upload.txt").write_text("hello\n")
        (work / "onboarding.json").write_text(json.dumps(_ONBOARDING_REQUEST))
        shims = work / "shims"
        shims.mkdir()
        env = _environment(journey_env, home, database=fixture["database"], shims=shims)
        if fixture.get("registry_backend"):
            # The registry backend is an explicit choice: DATABASE_URL alone never selects it.
            config = work / "pitwall.toml"
            config.write_text('[personal]\nbackend = "registry"\n')
            env["PITWALL_CONFIG_FILE"] = str(config)
        # Commands that record evidence write into the catalogue; give them a private copy.
        shutil.copytree(ROOT / "docs" / "models", work / "models")
        env["PITWALL_MODELS_DIR"] = str(work / "models")
        run = {"cwd": tmp, "env": env, "stdin": subprocess.DEVNULL}
        for before in fixture.get("before", []):
            subprocess.run(
                _argv(before[0], before[1:]), **run, capture_output=True, timeout=60, check=True
            )
        argv = _argv(program, fixture["argv"])
        if fixture.get("daemon"):
            process = subprocess.Popen(
                argv, **run, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True
            )
            try:
                process.wait(timeout=_DAEMON_WINDOW_S)
                alive = False
            except subprocess.TimeoutExpired:
                alive = True
                process.kill()
            out, err = process.communicate()  # reads and closes both pipes
            code, stdout, stderr = process.returncode, out.decode(), err.decode()
        else:
            result = subprocess.run(argv, **run, capture_output=True, text=True, timeout=120)
            code, alive, stdout, stderr = result.returncode, False, result.stdout, result.stderr
        then_code = None
        if "then" in fixture:
            then = subprocess.run(
                _argv(program, fixture["then"]["argv"]), **run, capture_output=True, timeout=60
            )
            then_code = then.returncode
        unknown = subprocess.run(
            [*argv, "--j36-not-a-flag"], **run, capture_output=True, text=True, timeout=60
        )
    return Outcome(code, alive, stdout, stderr, then_code, unknown.returncode, unknown.stderr)


def _checkout_state() -> str:
    return subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture(scope="module")
def checkout_before() -> str:
    return _checkout_state()


@pytest.fixture(scope="module")
def outcomes(journey_env: dict[str, str], checkout_before: str) -> dict[str, Outcome]:
    for program, binary in BINARIES.items():
        assert binary.exists(), f"{program} is not installed at {binary}"
    database = [key for key in sorted(FIXTURES) if FIXTURES[key]["database"]]
    local = [key for key in sorted(FIXTURES) if not FIXTURES[key]["database"]]
    results = {key: _run_one(key, journey_env) for key in database}
    with ThreadPoolExecutor(max_workers=8) as pool:
        results.update(
            zip(local, pool.map(lambda key: _run_one(key, journey_env), local), strict=True)
        )
    return results


@pytest.fixture(scope="module")
def exploration(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    home = tmp_path_factory.mktemp("j36-explore")
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "XDG_STATE_HOME": str(home / "state"),
        "XDG_CONFIG_HOME": str(home / "config"),
    }
    result = subprocess.run(
        [sys.executable, "-m", "tests.release.cli_explorer"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
        check=True,
    )
    return dict(json.loads(result.stdout))


def test_no_command_writes_into_the_checkout(
    outcomes: dict[str, Outcome], checkout_before: str
) -> None:
    assert _checkout_state() == checkout_before


def test_every_command_path_has_a_fixture(exploration: dict[str, Any]) -> None:
    explored = {
        f"{program}|{path}"
        for program, helps in exploration["helps"].items()
        for path in helps
        if path not in _INTERNAL
    }
    assert sorted(FIXTURES) == sorted(explored)
    for program, helps in exploration["helps"].items():
        for path, entry in helps.items():
            if path not in _INTERNAL:
                assert entry["code"] in (0, None), (program, path, entry["code"])


def test_every_argument_surface_is_registered_and_documented(
    exploration: dict[str, Any],
) -> None:
    from tools.release_acceptance import cli_arguments

    registered: dict[tuple[str, int], list[tuple[list[str], str]]] = {}
    for file, line, names, help_text in exploration["arguments"]:
        registered.setdefault((file, line), []).append((names, help_text))
    seen = {(op, file, line) for op, file, line, _name in exploration["events"]}
    surfaces = cli_arguments.discover_with_issues(ROOT)["surfaces"]
    assert len(surfaces) == 485
    for surface in surfaces:
        file, line_text = surface["source"].rsplit(":", 1)
        line = int(line_text)
        if surface["operation"] != "argument":
            assert (surface["operation"], file, line) in seen, surface["surface_id"]
            continue
        flags = surface["metadata"]["flags"]
        hits = [
            help_text
            for names, help_text in registered.get((file, line), [])
            if set(flags) <= set(names)
        ]
        assert hits, surface["surface_id"]
        for flag in flags:
            if flag.startswith("-"):
                assert any(flag in help_text for help_text in hits), (surface["surface_id"], flag)


@pytest.mark.parametrize("key", sorted(FIXTURES))
def test_command(key: str, outcomes: dict[str, Outcome]) -> None:
    fixture = FIXTURES[key]
    outcome = outcomes[key]
    if fixture.get("daemon"):
        assert outcome.alive, (key, outcome.code, outcome.stderr[-400:])
    else:
        assert outcome.code == fixture["exit"], (key, outcome.stdout[-400:], outcome.stderr[-400:])
        assert "Traceback" not in outcome.stderr, (key, outcome.stderr[-400:])
    if "json_keys" in fixture:
        payload = json.loads(outcome.stdout)
        missing = [name for name in fixture["json_keys"] if name not in payload]
        assert not missing, (key, missing)
    if "stdout_contains" in fixture:
        assert fixture["stdout_contains"] in outcome.stdout, key
    if "stderr_contains" in fixture:
        assert fixture["stderr_contains"] in outcome.stderr, key
    if "then" in fixture:
        assert outcome.then_code == fixture["then"]["exit"], key
    assert outcome.unknown_code == fixture.get("unknown_exit", 2), (key, outcome.unknown_stderr)
    assert "usage" in outcome.unknown_stderr.lower(), key
    assert "Traceback" not in outcome.unknown_stderr, key
