"""Native profile tests, translated from packages/pi-workbench/tests/native-profile.test.ts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pitwall.workbench.native_profile import (
    NativeProfileError,
    configure_native_profile,
    parse_frontmatter,
    validate_planning_role_profiles,
)
from pitwall.workbench.profile import CompiledProfile, compile_profile

PROFILE: dict[str, Any] = {
    "provider": "fixture",
    "modelId": "fixture-model",
    "endpoint": "http://127.0.0.1:1/v1",
    "api": "openai-completions",
    "keyless": "dummy",
    "servedContextTokens": 32768,
    "maxCompletionTokens": 4096,
    "reasoningLevel": "off",
    "resourceGroup": "fixture",
    "allowProviderFallback": False,
}


def compiled_in(root: Path, **changes: Any) -> CompiledProfile:
    return compile_profile("fixture", {**PROFILE, **changes}, root / "agent")


def write_project_file(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


@pytest.mark.parity
def test_writes_an_owned_read_only_native_scout_profile_with_exact_model_and_policy(
    tmp_path: Path,
) -> None:
    """Source: native-profile.test.ts 'writes an owned read-only native scout profile with exact model and policy'."""
    compiled = compiled_in(tmp_path)
    result = configure_native_profile(compiled, tmp_path)
    config = json.loads(result.profile_path.read_text())
    assert result.env["PITWALL_WORKBENCH_NATIVE_PROFILE"] == str(result.profile_path)
    assert "isolated: true" in result.agent_path.read_text()
    agents = compiled.agent_dir / "agents"
    assert "tools: read, edit, write, bash" in (agents / "workbench-worker.md").read_text()
    assert "tools: read, grep, find, ls" in (agents / "workbench-reviewer.md").read_text()
    subagents = json.loads((compiled.agent_dir / "subagents.json").read_text())
    assert subagents == {
        "schedulingEnabled": False,
        "workflowsEnabled": False,
        "agentMentions": "off",
        "disableDefaultAgents": True,
        "fallbackSubagent": False,
        "maxSubagentDepth": 0,
    }
    assert config["backend"] == "@tintinweb/pi-subagents"
    names = {path.name for path in compiled.agent_dir.iterdir()}
    assert {"models.json", ".pi-workbench-owned.json", "native-profile.json"} <= names


@pytest.mark.parity
@pytest.mark.parametrize("api", ["openai-completions", "openai-responses", "anthropic-messages"])
def test_accepts_the_supported_native_api(tmp_path: Path, api: str) -> None:
    """Source: native-profile.test.ts 'accepts the supported native API %s'."""
    compiled = compiled_in(tmp_path, api=api)
    result = configure_native_profile(compiled, tmp_path)
    config = json.loads(result.profile_path.read_text())
    assert config["profile"]["api"] == api
    assert config["providerConfig"]["api"] == api


@pytest.mark.parity
def test_planning_native_profile_gives_every_managed_child_the_read_only_tool_surface(
    tmp_path: Path,
) -> None:
    """Source: native-profile.test.ts 'planning native profile gives every managed child the read-only tool surface'."""
    compiled = compiled_in(tmp_path)
    result = configure_native_profile(compiled, tmp_path, planning=True)
    assert json.loads(result.profile_path.read_text())["planning"] is True
    for name in ("workbench-scout.md", "workbench-worker.md", "workbench-reviewer.md"):
        contents = (compiled.agent_dir / "agents" / name).read_text()
        assert "tools: read, grep, find, ls" in contents
        assert "tools: read, edit, write, bash" not in contents


@pytest.mark.parity
def test_rejects_edits_to_owned_native_profile_files(tmp_path: Path) -> None:
    """Source: native-profile.test.ts 'rejects edits to owned native profile files'."""
    compiled = compiled_in(tmp_path)
    configure_native_profile(compiled, tmp_path)
    (compiled.agent_dir / "native-profile.json").write_text("edited\n")
    with pytest.raises(NativeProfileError, match="native profile file conflict"):
        configure_native_profile(compiled, tmp_path)


@pytest.mark.parity
def test_rejects_a_symlinked_native_profile_leaf(tmp_path: Path) -> None:
    """Source: native-profile.test.ts 'rejects a symlinked native profile leaf'."""
    compiled = compiled_in(tmp_path)
    (compiled.agent_dir / "native-profile.json").symlink_to(tmp_path / "outside.json")
    with pytest.raises(NativeProfileError, match="must not be a symlink"):
        configure_native_profile(compiled, tmp_path)


@pytest.mark.parity
def test_rejects_a_project_scout_collision(tmp_path: Path) -> None:
    """Source: native-profile.test.ts 'rejects a project scout collision'."""
    compiled = compiled_in(tmp_path)
    write_project_file(tmp_path, ".pi/agents/workbench-scout.md", "user\n")
    with pytest.raises(NativeProfileError, match="native scout collision"):
        configure_native_profile(compiled, tmp_path)


@pytest.mark.parity
def test_rejects_project_subagent_settings_that_would_reopen_unmanaged_execution_paths(
    tmp_path: Path,
) -> None:
    """Source: native-profile.test.ts 'rejects project subagent settings that would reopen unmanaged execution paths'."""
    compiled = compiled_in(tmp_path)
    write_project_file(tmp_path, ".pi/subagents.json", json.dumps({"workflowsEnabled": True}))
    with pytest.raises(NativeProfileError, match="workflowsEnabled"):
        configure_native_profile(compiled, tmp_path)


@pytest.mark.parity
def test_rejects_unknown_project_backend_policy_keys_instead_of_letting_the_backend_ignore_them(
    tmp_path: Path,
) -> None:
    """Source: native-profile.test.ts 'rejects unknown project backend policy keys instead of letting the backend ignore them'."""
    compiled = compiled_in(tmp_path)
    write_project_file(tmp_path, ".pi/subagents.json", json.dumps({"worklfowsEnabled": False}))
    with pytest.raises(
        NativeProfileError, match="unknown project subagent setting worklfowsEnabled"
    ):
        configure_native_profile(compiled, tmp_path)


@pytest.mark.parity
def test_planning_rejects_unmanaged_project_agent_definitions(tmp_path: Path) -> None:
    """Source: native-profile.test.ts 'planning rejects unmanaged project agent definitions'."""
    compiled = compiled_in(tmp_path)
    write_project_file(
        tmp_path, ".pi/agents/unsafe-writer.md", "---\nname: unsafe-writer\ntools: write\n---\n"
    )
    with pytest.raises(NativeProfileError, match="unmanaged agent"):
        configure_native_profile(compiled, tmp_path, planning=True)


@pytest.mark.parity
def test_rejects_collisions_with_managed_writer_and_reviewer_roles(tmp_path: Path) -> None:
    """Source: native-profile.test.ts 'rejects collisions with managed writer and reviewer roles'."""
    compiled = compiled_in(tmp_path)
    write_project_file(
        tmp_path, ".pi/agents/unrelated.md", "---\nname: workbench-worker # collision\n---\n"
    )
    with pytest.raises(NativeProfileError, match="native role collision"):
        configure_native_profile(compiled, tmp_path)


@pytest.mark.parity
def test_parses_quoted_inline_comment_names_and_symlink_targets_for_collisions(
    tmp_path: Path,
) -> None:
    """Source: native-profile.test.ts 'parses quoted inline-comment names and symlink targets for collisions'."""
    compiled = compiled_in(tmp_path)
    (tmp_path / ".pi/agents").mkdir(parents=True)
    target = tmp_path / "target.md"
    target.write_text('---\nname: "workbench-scout" # owned collision\n---\n')
    (tmp_path / ".pi/agents/arbitrary.md").symlink_to(target)
    with pytest.raises(NativeProfileError, match="native scout collision"):
        configure_native_profile(compiled, tmp_path)


def test_parse_frontmatter_handles_missing_empty_and_bom_blocks() -> None:
    assert parse_frontmatter("no frontmatter\n") == {}
    assert parse_frontmatter("---\n---\nbody\n") == {}
    assert parse_frontmatter("﻿---\nname: a\r\ntools: read\r\n---\nbody") == {
        "name": "a",
        "tools": "read",
    }


def test_parse_frontmatter_reads_yaml_1_2_scalars() -> None:
    parsed = parse_frontmatter(
        "---\nisolation: off\nmode: on\nextensions: false\nisolated: true\n---\n"
    )
    assert parsed == {"isolation": "off", "mode": "on", "extensions": False, "isolated": True}


def test_planning_validation_rejects_a_writable_managed_role(tmp_path: Path) -> None:
    compiled = compiled_in(tmp_path)
    configure_native_profile(compiled, tmp_path, planning=True)
    model = "fixture/fixture-model"
    validate_planning_role_profiles(compiled.agent_dir, model)
    worker = compiled.agent_dir / "agents" / "workbench-worker.md"
    worker.write_text(
        worker.read_text().replace("tools: read, grep, find, ls", "tools: read, write")
    )
    with pytest.raises(NativeProfileError, match="only read, grep, find, and ls"):
        validate_planning_role_profiles(compiled.agent_dir, model)
    with pytest.raises(NativeProfileError, match="exact model other/model"):
        validate_planning_role_profiles(compiled.agent_dir, "other/model")
    worker.unlink()
    with pytest.raises(NativeProfileError, match="requires managed role"):
        validate_planning_role_profiles(compiled.agent_dir, model)


def test_frontmatter_parse_error_never_quotes_the_profile() -> None:
    text = "---\nname: x\ntoken: hunter2\napi_key: sk-SECRET123: x\n---\nbody\n"
    with pytest.raises(NativeProfileError) as caught:
        parse_frontmatter(text)
    message = str(caught.value)
    assert "line 4, column 22" in message
    assert "sk-SECRET123" not in message
    assert "hunter2" not in message
    assert caught.value.__cause__ is None
