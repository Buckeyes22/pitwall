from __future__ import annotations

import json
import shlex
from pathlib import Path

from pitwall.personal.deadline import redact_for_display, server_binary, wrap_start_command

_CONTRACT = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "runpod_v2_contract_2026-08-31.json").read_text()
)


def test_wrapper_terminates_after_ttl_then_execs_server_with_key() -> None:
    cmd = wrap_start_command(
        ["/app/llama-server"],
        ["--hf-repo", "org/model", "--alias", "m", "--port", "8000"],
        ttl_seconds=2700,
    )

    assert len(cmd) == 1
    script = cmd[0]
    assert "sleep 2700;" in script
    assert script.rstrip().endswith(
        'exec /app/llama-server --hf-repo org/model --alias m --port 8000 --api-key "$PITWALL_ENDPOINT_KEY"'
    )
    assert script.index("sleep") < script.index("exec ")


def test_terminate_uses_the_v2_action_endpoint() -> None:
    [script] = wrap_start_command(["/app/llama-server"], ["-m", "x"], ttl_seconds=60)
    action_path = _CONTRACT["paths"]["podAction"].replace("{id}", "$RUNPOD_POD_ID")
    assert f"https://api.runpod.io/v2/{action_path}" in script
    assert '\'{"action":"terminate"}\'' in script
    assert "-X DELETE" not in script


def test_stop_fallback_uses_the_v2_stop_action() -> None:
    [script] = wrap_start_command(["vllm", "serve"], ["org/model"], ttl_seconds=60, terminate=False)
    assert '\'{"action":"stop"}\'' in script
    assert "stop" in _CONTRACT["podActions"]


def test_timer_failures_reach_the_pod_log() -> None:
    [script] = wrap_start_command(["/app/llama-server"], ["-m", "x"], ttl_seconds=60)
    assert ">/dev/null" not in script
    assert "pitwall-deadline:" in script


def test_argv_is_shell_quoted() -> None:
    script = wrap_start_command(
        ["/app/llama-server"], ["--ctx-size", "32768", "--alias", "a b"], ttl_seconds=60
    )[0]
    assert shlex.quote("a b") in script


def test_server_binary_by_engine() -> None:
    assert server_binary("llama.cpp", "ghcr.io/ggml-org/llama.cpp:server-cuda") == [
        "/app/llama-server"
    ]
    assert server_binary("vllm", "vllm/vllm-openai:latest") == ["vllm", "serve"]
    assert server_binary("sglang", "lmsysorg/sglang:latest") == [
        "python3",
        "-m",
        "sglang.launch_server",
    ]


def test_redaction_hides_key_values() -> None:
    shown = redact_for_display(
        ["--api-key", "sk-live-secret", "--api-key", '"$PITWALL_ENDPOINT_KEY"']
    )
    assert shown == ["--api-key", "<redacted>", "--api-key", "<redacted>"]
