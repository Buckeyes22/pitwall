"""Caller-key recovery and actuator-side ownership, without provider calls."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from pitwall.agents import project_control
from pitwall.agents.managed_channel import LaunchRequest
from pitwall.agents.mcp_server import ToolError
from pitwall.agents.project_control import ProjectControl


class Orchestrator:
    def _require_child_channel(self, args: dict[str, Any]) -> str:
        return "fixture"

    def _launch_request(self, args: dict[str, Any], dispatch_id: str) -> LaunchRequest:
        return LaunchRequest(dispatch_id, args["route"], None, args["prompt"])

    def _wait(self, dispatch_id: str, *args: Any) -> dict[str, Any]:
        return {"event": "terminal", "dispatch_id": dispatch_id, "status": "succeeded"}


@pytest.fixture
def control(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[ProjectControl, list[str]]:
    launches: list[str] = []

    def launch(env: dict[str, str], request: LaunchRequest) -> dict[str, Any]:
        launches.append(request.dispatch_id)
        (tmp_path / "launches" / request.dispatch_id).mkdir(parents=True)
        return {"dispatch_id": request.dispatch_id}

    monkeypatch.setattr(project_control, "start_dispatch", launch)
    return ProjectControl({"PITWALL_AGENTS_STATE_HOME": str(tmp_path)}, Orchestrator()), launches


def call(control: ProjectControl, operation: str, **kwargs: Any) -> dict[str, Any]:
    return control.call(
        {
            "operation": operation,
            "controller_id": "project-a",
            "binding_hash": "a" * 64,
            "owner": "owner-1",
            "epoch": 1,
            "key": "build-1",
            **kwargs,
        },
        None,
        lambda _: None,
    )


def acquire(control: ProjectControl) -> None:
    assert call(control, "acquire", epoch=0)["epoch"] == 1


def test_lost_response_lookup_and_exact_replay_do_not_launch_twice(control):
    tool, launches = control
    acquire(tool)
    first = call(tool, "dispatch", request={"route": "a", "prompt": "build"})
    fresh = ProjectControl(tool.env, Orchestrator())
    assert call(fresh, "observe")["dispatch_id"] == first["dispatch_id"]
    assert call(fresh, "dispatch", request={"route": "a", "prompt": "build"}) == first
    assert launches == [first["dispatch_id"]]
    with pytest.raises(ToolError, match="conflict"):
        call(fresh, "dispatch", request={"route": "a", "prompt": "different"})


def test_replacement_fences_old_controller_even_for_new_action_key(control):
    tool, launches = control
    acquire(tool)
    assert call(tool, "acquire", owner="owner-2")["epoch"] == 2
    with pytest.raises(ToolError, match="stale"):
        call(tool, "dispatch", key="new-key", request={"route": "a", "prompt": "build"})
    assert launches == []
    assert call(tool, "observe", epoch=2, owner="owner-2")["event"] == "absent"


def test_interrupted_launch_intent_is_unknown_not_absent(control, monkeypatch):
    tool, launches = control
    acquire(tool)

    def crash(*args):
        raise RuntimeError("interrupted before acknowledgment")

    monkeypatch.setattr(project_control, "start_dispatch", crash)
    with pytest.raises(RuntimeError):
        call(tool, "dispatch", request={"route": "a", "prompt": "build"})
    assert call(tool, "observe")["event"] == "unknown"
    call(tool, "dispatch", request={"route": "a", "prompt": "build"})
    assert launches == []


def test_acquire_and_launch_share_the_actuator_lock(control, monkeypatch):
    tool, _ = control
    acquire(tool)
    entered, release, acquiring = threading.Event(), threading.Event(), threading.Event()
    original = project_control.start_dispatch

    def launch(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)

    def replace():
        acquiring.set()
        return call(tool, "acquire", owner="owner-2")

    monkeypatch.setattr(project_control, "start_dispatch", launch)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(call, tool, "dispatch", request={"route": "a", "prompt": "build"})
        assert entered.wait(5)
        replacement = pool.submit(replace)
        assert acquiring.wait(5)
        assert not replacement.done()
        release.set()
        first.result()
        assert replacement.result()["epoch"] == 2
    with pytest.raises(ToolError, match="stale"):
        call(tool, "observe")


def test_answer_reconciles_mailbox_write_before_receipt(control, monkeypatch):
    tool, _ = control
    acquire(tool)
    dispatch = call(tool, "dispatch", request={"route": "a", "prompt": "build"})
    answers = []

    def answer(*args, **kwargs):
        answers.append(args[1:4])
        return {"choice": "yes"}

    monkeypatch.setattr(project_control, "answer_once", answer)
    first = call(tool, "answer", key="answer-1", launch_key="build-1", ask_id="0001", choice="yes")
    assert first["dispatch_id"] == dispatch["dispatch_id"]
    assert call(tool, "observe", key="answer-1") == first
    assert (
        call(tool, "answer", key="answer-1", launch_key="build-1", ask_id="0001", choice="yes")
        == first
    )
    assert len(answers) == 1
    with pytest.raises(ToolError, match="conflict"):
        call(tool, "answer", key="answer-1", launch_key="build-1", ask_id="0001", choice="no")


@pytest.mark.parametrize("change", ["binding", "cwd"])
def test_binding_or_directory_cannot_change(control, monkeypatch, tmp_path, change):
    tool, _ = control
    acquire(tool)
    kwargs = {"binding_hash": "b" * 64} if change == "binding" else {}
    if change == "cwd":
        monkeypatch.chdir(tmp_path)
    with pytest.raises(ToolError, match="changed"):
        call(tool, "status", **kwargs)


def test_acquire_replay_and_compare_exchange(control):
    tool, _ = control
    acquire(tool)
    assert call(tool, "acquire", epoch=0)["epoch"] == 1
    with pytest.raises(ToolError, match="epoch changed"):
        call(tool, "acquire", epoch=0, owner="other")


@pytest.mark.parametrize(
    "enabled,child,visible", [(False, False, False), (True, False, True), (True, True, False)]
)
def test_project_control_is_opt_in_and_orchestrator_only(tmp_path, enabled, child, visible):
    import os

    from tests.agents.mcp_test_client import McpTestClient

    env = {"HOME": str(tmp_path), "PATH": os.environ.get("PATH", "")}
    if enabled:
        env["PITWALL_AGENTS_PROJECT_CONTROL"] = "1"
    if child:
        env["PITWALL_AGENTS_CHANNEL_DISPATCH_ID"] = "00000000-0000-4000-8000-000000000005"
        env["PITWALL_AGENTS_CHANNEL_STATE_ROOT"] = str(tmp_path / "state")
    client = McpTestClient(env)
    try:
        client.initialize()
        names = {tool["name"] for tool in client.request("tools/list")["result"]["tools"]}
        assert ("project_control" in names) == visible
        if visible:
            result = client.call(
                "project_control",
                {"operation": "status", "controller_id": "fixture", "binding_hash": "a" * 64},
            )["result"]
            assert result["structuredContent"]["epoch"] == 0
        else:
            result = client.call("project_control", {})
            assert result["error"]["code"] == -32602
    finally:
        client.close()
