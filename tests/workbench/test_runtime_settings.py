"""Runtime settings tests, translated from packages/pi-workbench/tests/runtime-settings.test.ts."""

from __future__ import annotations

import json
import math
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

import pytest

from pitwall.workbench.launcher import PiLaunchOptions, extension_path, launch_pi
from pitwall.workbench.profile import compile_profile, configure_provider_profile
from pitwall.workbench.runtime_settings import (
    RuntimeSettingsError,
    derive_compaction_settings,
    enforce_runtime_settings,
)
from tests.workbench.pi_support import (
    FixtureServer,
    RpcClient,
    chat_chunk,
    chat_usage,
    pinned_pi,
    requires_pi,
    sse_headers,
    sse_write,
)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def write_project_settings(root: Path, value: object) -> None:
    (root / ".pi").mkdir(exist_ok=True)
    (root / ".pi/settings.json").write_text(json.dumps(value))


@pytest.mark.parity
def test_retry_policy_preserves_compaction_and_rejects_project_override(tmp_path: Path) -> None:
    """Source: runtime-settings.test.ts 'retry policy preserves compaction and rejects project override'."""
    (tmp_path / "settings.json").write_text(json.dumps({"compaction": {"enabled": False}}))
    enforce_runtime_settings(tmp_path)
    settings = read_json(tmp_path / "settings.json")
    assert settings["compaction"]["enabled"] is False
    assert settings["retry"]["enabled"] is False
    for retry in ({"enabled": True}, {"enabled": "false"}, None):
        write_project_settings(tmp_path, {"retry": retry})
        with pytest.raises(RuntimeSettingsError, match="conflict"):
            enforce_runtime_settings(tmp_path, tmp_path)


@pytest.mark.parity
def test_profile_derives_pi_compaction_reserve_and_recent_context_settings(
    tmp_path: Path,
) -> None:
    """Source: runtime-settings.test.ts 'profile derives Pi compaction reserve and recent-context settings'."""
    profile = {
        "servedContextTokens": 32768,
        "maxCompletionTokens": 4096,
        "contextReserveTokens": 2048,
    }
    derived = derive_compaction_settings(profile)
    assert (derived.enabled, derived.reserve_tokens) == (True, 6144)
    assert (derived.keep_recent_tokens, derived.safety_reserve_tokens) == (10240, 2048)
    (tmp_path / "settings.json").write_text(
        json.dumps({"compaction": {"enabled": False, "keepRecentTokens": 1, "reserveTokens": 1}})
    )
    enforce_runtime_settings(tmp_path, None, profile)
    assert read_json(tmp_path / "settings.json")["compaction"] == {
        "enabled": True,
        "keepRecentTokens": 10240,
        "reserveTokens": 6144,
    }


@pytest.mark.parity
@pytest.mark.parametrize(
    ("name", "served", "completion"),
    [
        ("minimax-coding-plan", 204800, 128),
        ("zai-coding-plan", 202752, 128),
        ("alibaba-token-plan", 983616, 128),
    ],
)
def test_supported_hosted_profile_receives_derived_compaction_settings(
    tmp_path: Path, name: str, served: int, completion: int
) -> None:
    """Source: runtime-settings.test.ts 'supported hosted profile %s receives derived compaction settings'."""
    profile = {"servedContextTokens": served, "maxCompletionTokens": completion}
    derived = derive_compaction_settings(profile)
    assert derived.enabled is True
    assert derived.reserve_tokens == completion + min(completion, math.floor(served * 0.1))
    assert derived.keep_recent_tokens > 0
    enforce_runtime_settings(tmp_path, None, profile)
    assert read_json(tmp_path / "settings.json")["compaction"] == {
        "enabled": True,
        "reserveTokens": derived.reserve_tokens,
        "keepRecentTokens": derived.keep_recent_tokens,
    }, name


@pytest.mark.parity
def test_profile_rejects_conflicting_project_compaction_overrides(tmp_path: Path) -> None:
    """Source: runtime-settings.test.ts 'profile rejects conflicting project compaction overrides'."""
    profile = {
        "servedContextTokens": 32768,
        "maxCompletionTokens": 4096,
        "contextReserveTokens": 2048,
    }
    for compaction in ({"enabled": False}, {"reserveTokens": 1}, {"keepRecentTokens": 1}):
        write_project_settings(tmp_path, {"compaction": compaction})
        with pytest.raises(RuntimeSettingsError, match="compaction settings conflict"):
            enforce_runtime_settings(tmp_path, tmp_path, profile)


@pytest.mark.parity
def test_profile_derives_an_independent_http_inactivity_timeout(tmp_path: Path) -> None:
    """Source: runtime-settings.test.ts 'profile derives an independent HTTP inactivity timeout'."""
    profile = {
        "servedContextTokens": 32768,
        "maxCompletionTokens": 4096,
        "contextReserveTokens": 2048,
        "requestInactivityTimeoutMs": 75,
    }
    enforce_runtime_settings(tmp_path, None, profile)
    assert read_json(tmp_path / "settings.json")["httpIdleTimeoutMs"] == 75
    write_project_settings(tmp_path, {"httpIdleTimeoutMs": 80})
    with pytest.raises(RuntimeSettingsError, match="HTTP inactivity timeout conflicts"):
        enforce_runtime_settings(tmp_path, tmp_path, profile)


def test_enforcing_twice_leaves_the_settings_file_untouched(tmp_path: Path) -> None:
    profile = {"servedContextTokens": 32768, "maxCompletionTokens": 4096}
    enforce_runtime_settings(tmp_path, None, profile)
    first = (tmp_path / "settings.json").stat()
    enforce_runtime_settings(tmp_path, None, profile)
    assert (tmp_path / "settings.json").stat().st_mtime_ns == first.st_mtime_ns
    assert (tmp_path / "settings.json").stat().st_mode & 0o777 == 0o600


def test_runtime_settings_refuse_a_symlinked_settings_file(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text("{}")
    (tmp_path / "settings.json").symlink_to(target)
    with pytest.raises(RuntimeSettingsError, match="regular file"):
        enforce_runtime_settings(tmp_path)


def test_a_profile_leaving_no_recent_context_is_rejected() -> None:
    with pytest.raises(RuntimeSettingsError, match="no room for recent context"):
        derive_compaction_settings(
            {"servedContextTokens": 1000, "maxCompletionTokens": 400, "contextReserveTokens": 100}
        )


# -- live cases: a pinned Pi runtime against a fixture provider ---------------------------

SUMMARY = (
    "## Retained Task\nRepair add.mjs only and preserve CHECKPOINT_NONCE_7F31.\n\n"
    "## Criteria\n- add(2,3)=5\n- add(-2,3)=1\n\n## Changed Files\n- add.mjs only\n\n"
    "## Validation State\n- node --test add.test.mjs exit code 0\n\n"
    "## Next Action\n- NEXT_ACTION_CONTINUE_WITHOUT_RESTATING"
)


def named_sse(handler: BaseHTTPRequestHandler, event: str, data: dict[str, Any]) -> None:
    sse_write(handler, f"event: {event}\ndata: {json.dumps(data)}\n\n")


def respond_with_text(
    handler: BaseHTTPRequestHandler, api: str, count: int, text: str, prompt_tokens: int
) -> None:
    """Stream one assistant text reply in the wire format of ``api``."""
    sse_headers(handler)
    if api == "openai-completions":
        sse_write(handler, chat_chunk({"role": "assistant", "content": text}, "stop"))
        sse_write(handler, chat_chunk({}, "stop"))
        sse_write(handler, chat_usage(prompt_tokens, 4))
        sse_write(handler, "data: [DONE]\n\n")
    elif api == "openai-responses":
        ident = f"fixture-{count}"
        named_sse(
            handler,
            "response.created",
            {"type": "response.created", "response": {"id": ident, "status": "in_progress"}},
        )
        named_sse(
            handler,
            "response.output_item.added",
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {
                    "type": "message",
                    "id": f"message-{count}",
                    "role": "assistant",
                    "content": [],
                },
            },
        )
        named_sse(
            handler,
            "response.output_text.delta",
            {"type": "response.output_text.delta", "output_index": 0, "delta": text},
        )
        named_sse(
            handler,
            "response.output_item.done",
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": {
                    "type": "message",
                    "id": f"message-{count}",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": text, "annotations": []}],
                },
            },
        )
        named_sse(
            handler,
            "response.completed",
            {
                "type": "response.completed",
                "response": {
                    "id": ident,
                    "status": "completed",
                    "model": "fixture-model",
                    "usage": {
                        "input_tokens": prompt_tokens,
                        "output_tokens": 4,
                        "total_tokens": prompt_tokens + 4,
                    },
                    "output": [],
                },
            },
        )
    else:
        named_sse(
            handler,
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": f"fixture-{count}",
                    "type": "message",
                    "role": "assistant",
                    "model": "fixture-model",
                    "content": [],
                    "usage": {"input_tokens": prompt_tokens, "output_tokens": 0},
                },
            },
        )
        named_sse(
            handler,
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        )
        named_sse(
            handler,
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": text},
            },
        )
        named_sse(handler, "content_block_stop", {"type": "content_block_stop", "index": 0})
        named_sse(
            handler,
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 4},
            },
        )
        named_sse(handler, "message_stop", {"type": "message_stop"})


@pytest.mark.live
@pytest.mark.parity
@requires_pi
@pytest.mark.parametrize("api", ["openai-completions", "openai-responses", "anthropic-messages"])
def test_derived_settings_let_a_wrapped_pi_session_auto_compact_and_continue(
    tmp_path: Path, api: str
) -> None:
    """Source: runtime-settings.test.ts 'derived settings let a wrapped %s Pi session auto-compact and continue'."""
    bodies: list[str] = []

    def respond(handler: BaseHTTPRequestHandler, path: str, body: bytes) -> None:
        text = body.decode()
        bodies.append(text)
        summarize = "## Goal" in text or "structured context checkpoint" in text
        prompt_tokens = 100 if summarize else math.ceil(len(text) / 4) + 2000
        respond_with_text(
            handler, api, len(bodies), SUMMARY if summarize else "Acknowledged.", prompt_tokens
        )

    with FixtureServer(respond) as server:
        profile = {
            "provider": "fixture",
            "modelId": "fixture-model",
            "endpoint": server.url,
            "api": api,
            "keyless": "dummy",
            "servedContextTokens": 16384,
            "maxCompletionTokens": 128,
            "contextReserveTokens": 2048,
            "resourceGroup": f"small-context-{api}",
            "allowProviderFallback": False,
        }
        compiled = compile_profile(f"small-context-{api}", profile, tmp_path / "agent")
        policy = configure_provider_profile(compiled, tmp_path)
        assert read_json(compiled.agent_dir / "settings.json")["compaction"] == {
            "enabled": True,
            "reserveTokens": 2176,
            "keepRecentTokens": 6016,
        }
        client = RpcClient(
            launch_pi(
                PiLaunchOptions(
                    cwd=tmp_path,
                    profile=compiled,
                    pi_bin=pinned_pi(),
                    env=policy.env,
                    extension=extension_path("extension"),
                    runtime_dir=tmp_path / "runtime",
                )
            )
        )
        try:
            client.command("get_state")
            client.command("set_auto_retry", enabled=False)
            client.command("set_auto_compaction", enabled=True)
            long = (
                "CHECKPOINT_NONCE_7F31 CRITERIA_SUMMARY_AND_SUFFIX VALIDATION_THRESHOLD "
                "NEXT_ACTION_CONTINUE_WITHOUT_RESTATING " + "retained-context " * 700
            )

            def compactions() -> list[dict[str, Any]]:
                return [
                    e
                    for e in client.snapshot()
                    if e.get("type") == "compaction_end" and e.get("reason") == "threshold"
                ]

            for turn in range(1, 6):
                discarded = "DISCARDED_TURN_1 " if turn == 1 else ""
                client.command("prompt", message=f"{long}\n{discarded}SUFFIX_TURN_{turn}")
                client.until(lambda turn=turn: client.settled_count() >= turn, what=f"turn {turn}")
                if compactions():
                    break
            client.until(lambda: bool(compactions()), what="automatic compaction")
            compaction = compactions()[0]
            assert compaction["aborted"] is False
            for expected in (
                "CHECKPOINT_NONCE_7F31",
                "add(2,3)=5",
                "add(-2,3)=1",
                "add.mjs only",
                "node --test add.test.mjs exit code 0",
                "NEXT_ACTION_CONTINUE_WITHOUT_RESTATING",
            ):
                assert expected in compaction["result"]["summary"]
            messages = client.command("get_messages")["data"]["messages"]
            turns_before = client.settled_count()
            assert f"SUFFIX_TURN_{turns_before}" in json.dumps(messages)
            client.command(
                "prompt", message="Continue from the preserved checkpoint and retained suffix."
            )
            client.until(lambda: client.settled_count() >= turns_before + 1, what="continuation")
            continuation = bodies[-1]
            assert "CHECKPOINT_NONCE_7F31" in continuation
            assert f"SUFFIX_TURN_{turns_before}" in continuation
            assert "DISCARDED_TURN_1" not in continuation

            accounting_path = compiled.agent_dir / "native-accounting.jsonl"

            def accounting() -> list[dict[str, Any]]:
                if not accounting_path.exists():
                    return []
                return [
                    json.loads(line) for line in accounting_path.read_text().splitlines() if line
                ]

            def ids(kind: str) -> list[str]:
                return sorted(r["requestId"] for r in accounting() if r["type"] == kind)

            client.until(
                lambda: (
                    bool(ids("native_request"))
                    and ids("native_settled") == ids("native_request") == ids("native_released")
                ),
                timeout=5,
                what="accounting release",
            )
            records = accounting()
            assert {r["type"] for r in records} == {
                "native_request",
                "native_settled",
                "native_released",
            }
            for record in (r for r in records if r["type"] == "native_request"):
                assert record["acquiredAt"] >= record["queuedAt"]
                assert record["transportStartedAt"] >= record["acquiredAt"]
            assert all(
                isinstance(r["releasedAt"], int | float)
                for r in records
                if r["type"] == "native_released"
            )
            assert len(compactions()) == 1
            assert len(bodies) > 2
            events = client.snapshot()
            assert not any(e.get("type") == "extension_error" for e in events)
            assistant_ends = [
                e
                for e in events
                if e.get("type") == "message_end"
                and e.get("message", {}).get("role") == "assistant"
            ]
            assert all(e["message"]["stopReason"] == "stop" for e in assistant_ends)
        finally:
            client.close()


@pytest.mark.live
@pytest.mark.parity
@requires_pi
def test_actual_solo_pi_session_does_not_agent_retry_http429(tmp_path: Path) -> None:
    """Source: runtime-settings.test.ts 'actual solo Pi session does not agent-retry HTTP429'."""
    requests = 0

    def respond(handler: BaseHTTPRequestHandler, path: str, body: bytes) -> None:
        nonlocal requests
        requests += 1
        payload = json.dumps(
            {"error": {"message": "rate limit exceeded", "type": "rate_limit_error"}}
        ).encode()
        handler.send_response(429)
        handler.send_header("content-type", "application/json")
        handler.send_header("content-length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)

    with FixtureServer(respond) as server:
        profile = {
            "provider": "fixture",
            "modelId": "fixture",
            "api": "openai-completions",
            "endpoint": server.url,
            "keyless": "dummy",
            "servedContextTokens": 32768,
            "maxCompletionTokens": 128,
            "resourceGroup": "quota-test",
            "allowProviderFallback": False,
        }
        compiled = compile_profile("quota", profile, tmp_path / "agent")
        policy = configure_provider_profile(compiled)
        client = RpcClient(
            launch_pi(
                PiLaunchOptions(
                    cwd=tmp_path,
                    profile=compiled,
                    pi_bin=pinned_pi(),
                    env=policy.env,
                    extension=extension_path("extension"),
                    runtime_dir=tmp_path / "runtime",
                )
            )
        )
        try:
            client.send({"id": "prompt", "type": "prompt", "message": "say ok"})
            client.until(lambda: client.settled_count() >= 1, timeout=10, what="agent_settled")
            assert requests == 1
            assert not any(e.get("type") == "auto_retry_start" for e in client.snapshot())
        finally:
            client.close()
