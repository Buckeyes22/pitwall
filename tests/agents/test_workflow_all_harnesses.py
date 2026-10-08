"""Workflow support for every registered harness (Task 2.6)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from pitwall.agents.harnesses import get_adapter
from pitwall.agents.registry import load_registry
from pitwall.agents.scheduler import run_workflow
from pitwall.agents.workflow import WorkflowError
from tests.agents.test_workflow import validate

ROOT = Path(__file__).resolve().parents[2]
FAKE_HARNESS = ROOT / "tests" / "agents" / "fixtures" / "fake_harness.py"
PROMPT = Path("/tmp/workflow-prompt.md")

ALL_HARNESSES = (
    "agy",
    "claude",
    "cline",
    "codex",
    "dsh",
    "goose",
    "grok",
    "hermes",
    "kimi",
    "muse",
    "opencode",
    "pi",
    "qwen",
    "zcode",
)
NEW_HARNESSES = ("agy", "cline", "dsh", "goose", "hermes", "muse", "qwen")

# harness -> (model, effort or None, expected argv). The flags are the ones each
# CLI documents: agy --model/--effort, cline -m/--model and --thinking,
# goose run --model, hermes chat -m/--model, muse exec --model and
# --reasoning-effort, qwen -m/--model. dsh boots a profile and forwards the
# remaining arguments to the profile's app.
EXPECTED: dict[str, tuple[str, str | None, list[str]]] = {
    "agy": (
        "gemini-3.8-flash",
        "high",
        [str(PROMPT), "--model", "gemini-3.8-flash", "--effort", "high"],
    ),
    "claude": ("sonnet", "high", [str(PROMPT), "--model", "sonnet", "--effort", "high"]),
    "cline": ("anthropic/x", "low", [str(PROMPT), "--model", "anthropic/x", "--thinking", "low"]),
    "codex": (
        "gpt-5.6-sol",
        "high",
        [str(PROMPT), "-m", "gpt-5.6-sol", "-c", "model_reasoning_effort=high"],
    ),
    "dsh": ("deepseek-x", None, [str(PROMPT), "--model", "deepseek-x"]),
    "goose": ("goose-x", None, [str(PROMPT), "--model", "goose-x"]),
    "grok": ("grok-4.7", "low", [str(PROMPT), "--model", "grok-4.7", "--effort", "low"]),
    "hermes": ("hermes-x", None, [str(PROMPT), "--model", "hermes-x"]),
    "kimi": ("kimi-code/k3", None, [str(PROMPT), "--model", "kimi-code/k3"]),
    "muse": (
        "muse-spark-1.3",
        "max",
        [str(PROMPT), "--model", "muse-spark-1.3", "--reasoning-effort", "max"],
    ),
    "opencode": ("test/model-1", "high", ["test/model-1", str(PROMPT), "--variant", "high"]),
    "pi": ("qwen-local", "low", [str(PROMPT), "--model", "qwen-local", "--thinking", "low"]),
    "qwen": ("qwen-x", None, [str(PROMPT), "--model", "qwen-x"]),
    "zcode": ("zcode-default", None, [str(PROMPT)]),
}


def test_registry_and_test_matrix_cover_the_same_registered_harnesses() -> None:
    assert set(load_registry()["harnesses"]) == set(ALL_HARNESSES) == set(EXPECTED)


@pytest.mark.parametrize("harness", ALL_HARNESSES)
def test_every_harness_builds_workflow_args(harness: str) -> None:
    model, effort, expected = EXPECTED[harness]
    adapter = get_adapter(harness)
    assert adapter.workflow_args(model, effort, PROMPT) == expected
    if effort is None:
        assert adapter.supported_efforts is None
    else:
        assert adapter.supported_efforts is not None
        registry_values = load_registry()["harnesses"][harness]["effort"]["values"]
        if registry_values:
            assert adapter.supported_efforts == frozenset(registry_values)


# Recorded from the pre-change scheduler._harness_args before the refactor.
PRE_CHANGE_SIX = {
    ("codex", "gpt-5.6-sol", "high"): [
        str(PROMPT), "-m", "gpt-5.6-sol", "-c", "model_reasoning_effort=high",
    ],
    ("codex", "gpt-5.6-sol", None): [str(PROMPT), "-m", "gpt-5.6-sol"],
    ("claude", "sonnet", "low"): [str(PROMPT), "--model", "sonnet", "--effort", "low"],
    ("claude", "sonnet", None): [str(PROMPT), "--model", "sonnet"],
    ("grok", "grok-4.7", "xhigh"): [str(PROMPT), "--model", "grok-4.7", "--effort", "xhigh"],
    ("grok", "grok-4.7", None): [str(PROMPT), "--model", "grok-4.7"],
    ("kimi", "kimi-code/k3", None): [str(PROMPT), "--model", "kimi-code/k3"],
    ("opencode", "p/m", "high"): ["p/m", str(PROMPT), "--variant", "high"],
    ("opencode", "p/m", None): ["p/m", str(PROMPT)],
    ("pi", "qwen-local", "low"): [str(PROMPT), "--model", "qwen-local", "--thinking", "low"],
    ("pi", "qwen-local", None): [str(PROMPT), "--model", "qwen-local"],
}  # fmt: skip


@pytest.mark.parametrize(("case", "expected"), list(PRE_CHANGE_SIX.items()))
def test_existing_six_argument_order_unchanged(
    case: tuple[str, str, str | None], expected: list[str]
) -> None:
    harness, model, effort = case
    assert get_adapter(harness).workflow_args(model, effort, PROMPT) == expected


def _body(harness: str, model: str, effort: str | None) -> dict[str, Any]:
    route: dict[str, Any] = {"provider": harness, "model": model}
    if effort is not None:
        route["effort"] = effort
    return {
        "schemaVersion": 1,
        "name": "all-harnesses",
        "tasks": {"one": {"route": route, "mode": "read", "prompt": {"text": "go"}}},
    }


def _validate(body: dict[str, Any], tmp_path: Path) -> Any:
    return validate(body, tmp_path=tmp_path, registry=load_registry())


NO_EFFORT = ("kimi", "dsh", "goose", "hermes", "qwen")


@pytest.mark.parametrize("harness", NO_EFFORT)
def test_effort_rejected_where_unsupported(harness: str, tmp_path: Path) -> None:
    assert get_adapter(harness).supported_efforts is None
    model = EXPECTED[harness][0]
    with pytest.raises(WorkflowError, match=f"harness '{harness}' has no effort control"):
        _validate(_body(harness, model, "high"), tmp_path)


@pytest.mark.parametrize("harness", ["muse", "agy"])
def test_model_bound_harness_rejects_other_model(harness: str, tmp_path: Path) -> None:
    bound_model = EXPECTED[harness][0]
    _validate(_body(harness, bound_model, None), tmp_path)
    with pytest.raises(WorkflowError, match=f"model-bound harness '{harness}'"):
        _validate(_body(harness, "some-other-model", None), tmp_path)


@pytest.mark.parametrize("harness", ALL_HARNESSES)
def test_every_harness_is_workflow_capable(harness: str, tmp_path: Path) -> None:
    model, effort, _ = EXPECTED[harness]
    # The registry declares no opencode effort values, so it takes none here.
    _validate(_body(harness, model, None if harness == "opencode" else effort), tmp_path)


@pytest.mark.parametrize("harness", NEW_HARNESSES)
def test_workflow_runs_with_fake_harness_binaries(harness: str, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "seed.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "seed"], check=True)

    fake = tmp_path / f"fake-{harness}"
    fake.write_bytes(FAKE_HARNESS.read_bytes())
    fake.chmod(0o755)
    args_file = tmp_path / "args.bin"
    adapter = get_adapter(harness)
    assert adapter.binary_override_env is not None
    env = {
        "HOME": str(tmp_path / "home"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "PYTHONPYCACHEPREFIX": str(tmp_path / "pycache"),
        "PYTHONPATH": str(ROOT / "src"),
        "PATH": "/usr/bin:/bin",
        "PITWALL_AGENTS_UNRESTRICTED": "1",
        "FAKE_ARGS_FILE": str(args_file),
        adapter.binary_override_env: str(fake),
    }
    model, effort, _ = EXPECTED[harness]
    task = {
        "route": {"provider": harness, "model": model},
        "mode": "read",
        "prompt": {"text": "do the task"},
    }
    if effort is not None:
        task["route"]["effort"] = effort
    body = {
        "schemaVersion": 1,
        "name": f"two-node-{harness}",
        "tasks": {"first": task, "second": {**task, "dependsOn": ["first"]}},
    }
    path = repo / "workflow.json"
    path.write_text(json.dumps(body), encoding="utf-8")

    state = run_workflow(path, host="copilot", repo_root=repo, env=env, registry=load_registry())

    assert state["status"] == "succeeded", state
    assert state["tasks"]["first"]["state"] == "succeeded"
    assert state["tasks"]["second"]["state"] == "succeeded"
    argv = args_file.read_bytes().split(b"\0")
    assert model.encode() in argv
