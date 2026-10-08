"""Profile compilation tests, translated from packages/pi-workbench/tests/profile.test.ts."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from pitwall.workbench.launcher import LauncherError, PiLaunchOptions, launch_pi
from pitwall.workbench.profile import (
    ProfileError,
    compile_profile,
    configure_provider_profile,
    profile_from_config,
)
from tests.workbench.pi_support import (
    EXAMPLE_PROFILES,
    RpcClient,
    pinned_pi,
    requires_pi,
)

VALID: dict[str, Any] = {
    "provider": "fixture",
    "modelId": "fixture-model",
    "endpoint": "http://127.0.0.1:1/v1",
    "api": "openai-completions",
    "apiKeyEnv": "FIXTURE_KEY",  # pragma: allowlist secret
    "servedContextTokens": 32768,
    "maxCompletionTokens": 4096,
    "reasoningLevel": "high",
    "resourceGroup": "fixture",
    "allowProviderFallback": False,
    "compat": {"chatTemplateKwargs": {"enable_thinking": {"$var": "thinking.enabled"}}},
}


def profile_with(**changes: Any) -> dict[str, Any]:
    merged = {**VALID, **changes}
    return {key: value for key, value in merged.items() if value is not None}


def rpc_state(compiled: Any, cwd: Path, env: dict[str, str]) -> str:
    """Start pinned Pi in RPC mode on the compiled profile and return its get_state reply."""
    client = RpcClient(
        launch_pi(
            PiLaunchOptions(
                cwd=cwd,
                profile=compiled,
                pi_bin=pinned_pi(),
                env=env,
                runtime_dir=cwd / "runtime",
            )
        )
    )
    try:
        client.send({"id": "state", "type": "get_state"})
        client.until(
            lambda: any(e.get("command") == "get_state" for e in client.snapshot()),
            timeout=15,
            what="get_state",
        )
        return json.dumps(client.snapshot(), separators=(",", ":"))
    finally:
        client.close()


@pytest.mark.parity
def test_compiles_exact_model_and_env_ref_auth_into_isolated_pi_config(tmp_path: Path) -> None:
    """Source: profile.test.ts 'compiles exact model and env-ref auth into isolated Pi config'."""
    result = compile_profile(
        "local-coder",
        profile_with(samplingParams={"providerSpecificKnob": "preserved"}),
        tmp_path / "agent",
    )
    assert result.provider["apiKey"] == "$FIXTURE_KEY"
    written = json.loads(result.models_path.read_text())
    assert written["providers"]["fixture"]["models"][0]["id"] == "fixture-model"
    model = result.provider["models"][0]
    assert model["compat"]["chatTemplateKwargs"]["enable_thinking"] == {"$var": "thinking.enabled"}
    assert model["samplingParams"] == {"providerSpecificKnob": "preserved"}


@pytest.mark.parity
@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"modelId": "SET_FROM_SERVER_DISCOVERY"}, "exact modelId"),
        ({"allowProviderFallback": True}, "fallback"),
        ({"api": "bogus"}, "unsupported api"),
        ({"unknown": True}, "unknown profile field"),
        ({"apiKeyEnv": None, "keyless": None}, "keyless policy"),
    ],
)
def test_rejects_placeholder_or_fallback_profiles(
    tmp_path: Path, changes: dict[str, Any], message: str
) -> None:
    """Source: profile.test.ts 'rejects placeholder or fallback profiles'."""
    with pytest.raises(ProfileError, match=message):
        compile_profile("local-coder", profile_with(**changes), tmp_path / "agent")


@pytest.mark.parity
@pytest.mark.parametrize("name", ["PATH", "PITWALL_WORKBENCH_RESTRICTED_CREDENTIAL_ENV"])
def test_rejects_reserved_credential_environment_names(tmp_path: Path, name: str) -> None:
    """Source: profile.test.ts 'rejects placeholder or fallback profiles' (reserved apiKeyEnv cases)."""
    with pytest.raises(ProfileError, match="non-reserved"):
        compile_profile("local-coder", profile_with(apiKeyEnv=name), tmp_path / "agent")


@pytest.mark.parity
def test_rejects_endpoint_credential_and_query_channels_before_creating_state(
    tmp_path: Path,
) -> None:
    """Source: profile.test.ts 'rejects endpoint credential and query channels before creating state'."""
    for endpoint in (
        "http://user:secret@localhost/v1",  # pragma: allowlist secret
        "http://localhost/v1?api_key=secret",  # pragma: allowlist secret
        "http://localhost/v1#secret",
    ):
        with pytest.raises(ProfileError, match="credentials must use apiKeyEnv"):
            compile_profile("local-coder", profile_with(endpoint=endpoint), tmp_path / "agent")
    assert not (tmp_path / "agent").exists()


@pytest.mark.live
@pytest.mark.parity
@requires_pi
def test_generated_config_is_loadable_by_pinned_pi_runtime(tmp_path: Path) -> None:
    """Source: profile.test.ts 'generated config is loadable by pinned Pi runtime'."""
    compiled = compile_profile("local-coder", VALID, tmp_path / "agent")
    output = rpc_state(compiled, tmp_path, {"FIXTURE_KEY": "fixture"})
    assert '"provider":"fixture"' in output
    assert '"id":"fixture-model"' in output


@pytest.mark.live
@pytest.mark.parity
@requires_pi
@pytest.mark.skipif(not EXAMPLE_PROFILES.is_file(), reason="example profile file is not present")
def test_example_profile_compiles_and_is_accepted_by_the_pinned_pi_runtime(
    tmp_path: Path,
) -> None:
    """Source: profile.test.ts 'example profile compiles and is accepted by the pinned Pi runtime'."""
    example = json.loads(EXAMPLE_PROFILES.read_text())
    profile = {
        **example["profiles"]["local-coder"],
        "modelId": "fixture-model",
        "endpoint": "http://127.0.0.1:1/v1",
    }
    compiled = compile_profile("local-coder", profile, tmp_path / "agent")
    credential = profile.get("apiKeyEnv")
    output = rpc_state(compiled, tmp_path, {credential: "fixture"} if credential else {})
    assert '"provider":"local-openai"' in output
    assert '"id":"fixture-model"' in output


@pytest.mark.parity
def test_reopens_owned_directory_without_replacing_session_files(tmp_path: Path) -> None:
    """Source: profile.test.ts 'reopens owned directory without replacing session files'."""
    agent = tmp_path / "agent"
    first = compile_profile("local-coder", VALID, agent)
    (agent / "session.jsonl").write_text("keep\n")
    second = compile_profile("local-coder", VALID, agent)
    assert (agent / "session.jsonl").read_text() == "keep\n"
    assert second.models_path == first.models_path


@pytest.mark.parity
def test_refuses_to_follow_symlinked_owned_files(tmp_path: Path) -> None:
    """Source: profile.test.ts 'refuses to follow symlinked owned files'."""
    agent = tmp_path / "agent"
    compile_profile("local-coder", VALID, agent)
    target = tmp_path / "models-target.json"
    target.write_text((agent / "models.json").read_text())
    (agent / "models.json").unlink()
    (agent / "models.json").symlink_to(target)
    with pytest.raises(ProfileError, match="regular file"):
        compile_profile("local-coder", VALID, agent)
    assert "fixture-model" in target.read_text()


@pytest.mark.parity
def test_rejects_edited_models_and_unowned_directories(tmp_path: Path) -> None:
    """Source: profile.test.ts 'rejects edited models and unowned directories'."""
    agent = tmp_path / "agent"
    compile_profile("local-coder", VALID, agent)
    (agent / "models.json").write_text("edited\n")
    with pytest.raises(ProfileError, match="conflict"):
        compile_profile("local-coder", VALID, agent)
    unowned = tmp_path / "unowned"
    unowned.mkdir(mode=0o700)
    (unowned / "x").write_text("x")
    with pytest.raises(ProfileError, match="unowned"):
        compile_profile("local-coder", VALID, unowned)


@pytest.mark.parity
def test_rejects_symlink_agent_directories(tmp_path: Path) -> None:
    """Source: profile.test.ts 'rejects symlink agent directories'."""
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(ProfileError, match="real directory"):
        compile_profile("local-coder", VALID, link)


@pytest.mark.parity
def test_launch_rejects_missing_credential_before_spawning_pi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Source: profile.test.ts 'launch rejects missing credential before spawning Pi'."""
    compiled = compile_profile("local-coder", VALID, tmp_path / "agent")
    monkeypatch.delenv("FIXTURE_KEY", raising=False)
    with pytest.raises(LauncherError, match="missing credential"):
        launch_pi(PiLaunchOptions(cwd=tmp_path, profile=compiled, pi_bin=Path("/bin/true")))


@pytest.mark.parity
@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_launcher_validates_credentials_supplied_through_launch_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Source: profile.test.ts 'launcher validates credentials supplied through launch options'."""
    monkeypatch.delenv("FIXTURE_KEY", raising=False)
    compiled = compile_profile("local-coder", VALID, tmp_path / "agent")
    script = tmp_path / "noop.mjs"
    script.write_text("")
    child = launch_pi(
        PiLaunchOptions(
            cwd=tmp_path, profile=compiled, pi_bin=script, env={"FIXTURE_KEY": "option-only"}
        )
    )
    child.kill()
    child.wait()
    for stream in (child.stdin, child.stdout, child.stderr):
        if stream:
            stream.close()


@pytest.mark.parity
def test_profile_hash_detects_nested_profile_changes_on_reopen(tmp_path: Path) -> None:
    """Source: profile.test.ts 'profile hash detects nested profile changes on reopen'."""
    agent = tmp_path / "agent"
    compile_profile("local-coder", VALID, agent)
    with pytest.raises(ProfileError, match="conflict"):
        compile_profile("local-coder", profile_with(resourceGroup="changed"), agent)


@pytest.mark.parity
def test_rejects_invalid_input_modality_metadata(tmp_path: Path) -> None:
    """Source: profile.test.ts 'rejects invalid input modality metadata'."""
    with pytest.raises(ProfileError, match="inputModalities"):
        compile_profile("local-coder", profile_with(inputModalities=["image"]), tmp_path / "a")


@pytest.mark.parity
def test_emits_explicit_dummy_auth_for_deliberate_keyless_profiles(tmp_path: Path) -> None:
    """Source: profile.test.ts 'emits explicit dummy auth for deliberate keyless profiles'."""
    result = compile_profile(
        "local-coder", profile_with(apiKeyEnv=None, keyless="dummy"), tmp_path / "agent"
    )
    assert result.provider["apiKey"] == "dummy"  # pragma: allowlist secret


@pytest.mark.parity
def test_preserves_reasoning_capability_when_requested_level_is_off(tmp_path: Path) -> None:
    """Source: profile.test.ts 'preserves reasoning capability when requested level is off'."""
    result = compile_profile("local-coder", profile_with(reasoningLevel="off"), tmp_path / "agent")
    assert result.provider["models"][0]["reasoning"] is True


@pytest.mark.parity
def test_rejects_non_off_thinking_when_reasoning_capability_is_disabled(tmp_path: Path) -> None:
    """Source: profile.test.ts 'rejects non-off thinking when reasoning capability is disabled'."""
    with pytest.raises(ProfileError, match="reasoning capability"):
        compile_profile(
            "local-coder", profile_with(reasoning=False, reasoningLevel="high"), tmp_path / "a"
        )


@pytest.mark.parity
def test_rejects_a_profile_whose_output_and_safety_reserve_consume_the_served_context(
    tmp_path: Path,
) -> None:
    """Source: profile.test.ts 'rejects a profile whose output and safety reserve consume the served context'."""
    with pytest.raises(ProfileError, match="context safety reserve"):
        compile_profile(
            "local-coder",
            profile_with(
                servedContextTokens=4096, maxCompletionTokens=2048, contextReserveTokens=2048
            ),
            tmp_path / "a",
        )


def test_profile_files_are_created_private(tmp_path: Path) -> None:
    result = compile_profile("local-coder", VALID, tmp_path / "agent")
    for name in ("models.json", ".pi-workbench-owned.json"):
        assert (result.agent_dir / name).stat().st_mode & 0o777 == 0o600
    assert result.agent_dir.stat().st_mode & 0o077 == 0


def test_reopen_rechecks_file_permissions(tmp_path: Path) -> None:
    agent = tmp_path / "agent"
    compile_profile("local-coder", VALID, agent)
    (agent / "models.json").chmod(0o644)
    with pytest.raises(ProfileError, match="permissions"):
        compile_profile("local-coder", VALID, agent)


def test_rejects_a_broad_agent_directory(tmp_path: Path) -> None:
    agent = tmp_path / "agent"
    agent.mkdir(mode=0o755)
    agent.chmod(0o755)
    with pytest.raises(ProfileError, match="too broad"):
        compile_profile("local-coder", VALID, agent)


def test_rejects_non_kebab_profile_names(tmp_path: Path) -> None:
    with pytest.raises(ProfileError, match="kebab case"):
        compile_profile("Local Coder", VALID, tmp_path / "agent")


def test_profile_from_config_requires_schema_and_name() -> None:
    config = {"schemaVersion": 1, "profiles": {"a": VALID}}
    assert profile_from_config(config, "a") == VALID
    with pytest.raises(ProfileError, match="profile not found: b"):
        profile_from_config(config, "b")
    with pytest.raises(ProfileError, match="schemaVersion"):
        profile_from_config({"schemaVersion": 2, "profiles": {}}, "a")


def test_configure_provider_profile_writes_private_profile_and_settings(tmp_path: Path) -> None:
    compiled = compile_profile("local-coder", VALID, tmp_path / "agent")
    paths = configure_provider_profile(compiled, tmp_path)
    assert paths.env == {"PITWALL_WORKBENCH_PROVIDER_PROFILE": str(paths.profile_path)}
    assert paths.profile_path.stat().st_mode & 0o777 == 0o600
    written = json.loads(paths.profile_path.read_text())
    assert written["profile"]["modelId"] == "fixture-model"
    assert (
        json.loads((compiled.agent_dir / "settings.json").read_text())["retry"]["enabled"] is False
    )
    assert configure_provider_profile(compiled, tmp_path) == paths
    paths.profile_path.write_text("edited\n")
    with pytest.raises(ProfileError, match="different content"):
        configure_provider_profile(compiled, tmp_path)
