"""Accounting tests, translated from accounting.test.ts and usage.test.ts."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from pitwall.workbench.accounting import append_accounting, safe_usage, shape_payload, usage_report


@pytest.mark.parity
def test_records_payload_shape_without_transcript_text() -> None:
    """Source: accounting.test.ts 'records payload shape without transcript text'."""
    result = shape_payload(
        {
            "reasoning_effort": "high",
            "chat_template_kwargs": {"enable_thinking": True},
            "messages": [
                {"role": "user", "content": "secret prompt"},
                {
                    "role": "assistant",
                    "content": [{"type": "image_url", "image_url": {"url": "x"}}],
                },
            ],
            "tools": [{"name": "read"}],
        }
    )
    assert result.message_count == 2
    assert result.tool_count == 1
    assert result.image_count == 1
    assert "secret prompt" not in " ".join(result.content_hashes)
    assert result.reasoning_effort == "high"
    assert result.chat_template_kwargs == {"enable_thinking": True}


@pytest.mark.parity
def test_does_not_retain_secrets_placed_in_otherwise_allowed_reasoning_fields() -> None:
    """Source: accounting.test.ts 'does not retain secrets placed in otherwise allowed reasoning fields'."""
    sentinel = "private-credential-sentinel"  # pragma: allowlist secret
    result = shape_payload(
        {
            "reasoning_effort": sentinel,
            "chat_template_kwargs": {
                "enable_thinking": sentinel,
                "preserve_thinking": {"token": sentinel},
                "reasoning_effort": sentinel,
                "token": sentinel,
            },
            "messages": [{"role": sentinel, "content": sentinel}],
        }
    )
    assert sentinel not in json.dumps(asdict(result))
    assert result.chat_template_kwargs == {}
    assert result.reasoning_effort is None
    assert result.roles == {"unknown": 1}


@pytest.mark.parity
def test_separates_serialized_system_tools_and_conversation_bytes_without_calling_them_tokens() -> (
    None
):
    """Source: accounting.test.ts 'separates serialized system, tools, and conversation bytes without calling them tokens'."""
    result = shape_payload(
        {
            "messages": [
                {"role": "system", "content": "stock instructions"},
                {"role": "user", "content": "task"},
            ],
            "tools": [{"type": "function", "function": {"name": "read"}}],
        }
    )
    assert result.system_bytes > 0
    assert result.tools_bytes > 0
    assert result.conversation_bytes > 0
    assert result.serialized_bytes > result.system_bytes + result.conversation_bytes


@pytest.mark.parity
def test_accounts_api_specific_system_and_input_fields_without_retaining_prompt_text() -> None:
    """Source: accounting.test.ts 'accounts API-specific system and input fields without retaining prompt text'."""
    anthropic = shape_payload(
        {
            "system": [{"type": "text", "text": "anthropic private system prompt"}],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "private task"},
                        {"type": "image", "source": {"data": "secret-image"}},
                    ],
                }
            ],
        }
    )
    assert anthropic.system_bytes > 0
    assert anthropic.conversation_bytes > 0
    assert anthropic.message_count == 1
    assert anthropic.image_count == 1
    assert "private" not in json.dumps(asdict(anthropic))

    responses = shape_payload(
        {
            "instructions": "responses private instructions",
            "input": [
                {
                    "role": "developer",
                    "content": [{"type": "input_text", "text": "private developer prompt"}],
                },
                {"role": "user", "content": [{"type": "input_image", "image_url": "data:secret"}]},
            ],
        }
    )
    assert responses.system_bytes > 0
    assert responses.message_count == 2
    assert responses.roles["developer"] == 1
    assert responses.image_count == 1
    assert "private" not in json.dumps(asdict(responses))


def test_scalar_responses_input_counts_towards_conversation_without_retaining_text() -> None:
    result = shape_payload({"input": "private scalar prompt"})
    assert result.conversation_bytes > 0
    assert result.message_count == 0
    assert len(result.content_hashes) == 1
    assert "private" not in json.dumps(asdict(result))


@pytest.mark.parity
def test_restricts_an_existing_accounting_file_and_rejects_symlink_destinations(
    tmp_path: Path,
) -> None:
    """Source: accounting.test.ts 'restricts an existing accounting file and rejects symlink destinations'."""
    file = tmp_path / "accounting.jsonl"
    file.write_text("")
    file.chmod(0o644)
    append_accounting(file, {"type": "fixture"})
    assert file.stat().st_mode & 0o777 == 0o600
    link = tmp_path / "link.jsonl"
    link.symlink_to(file)
    with pytest.raises(OSError, match="Too many levels of symbolic links|symbolic"):
        append_accounting(link, {"type": "unwanted"})
    assert file.read_text() == '{"type":"fixture"}\n'


def test_safe_usage_keeps_only_known_finite_non_negative_fields() -> None:
    reported = {
        "input": 4,
        "output": 2,
        "cost": 100,
        "note": "hidden",
    }
    assert safe_usage(reported) == {"input": 4, "output": 2}
    assert safe_usage({"input": -1, "output": float("inf"), "reasoning": True}) is None
    assert safe_usage(None) is None


@pytest.mark.parity
def test_usage_preserves_unknown_values_and_counts_only_reported_usage(tmp_path: Path) -> None:
    """Source: usage.test.ts 'usage preserves unknown values and counts only reported usage'."""
    path = tmp_path / "usage.jsonl"
    records = [
        {"type": "native_request", "queueWaitMs": 3},
        {"type": "native_settled", "usage": None},
        {
            "type": "native_settled",
            "usage": {"input": 4, "output": 2, "cost": 100, "note": "hidden"},
        },
    ]
    path.write_text("\n".join(json.dumps(record) for record in records))
    report = usage_report(path)
    assert report.usage == {"input": 4, "output": 2}
    assert report.unavailable == 1
    assert report.queue_wait_ms == 3
    assert report.accounts == {"unattributed": {"input": 4, "output": 2}}
    assert report.source == "provider-reported"
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(OSError, match="symbolic|Too many"):
        usage_report(link)


@pytest.mark.parity
def test_usage_treats_pi_default_all_zero_usage_as_unavailable(tmp_path: Path) -> None:
    """Source: usage.test.ts 'usage treats Pi default all-zero usage as unavailable'."""
    path = tmp_path / "usage.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "native_settled",
                "usage": {
                    "input": 0,
                    "output": 0,
                    "cacheRead": 0,
                    "cacheWrite": 0,
                    "totalTokens": 0,
                },
            }
        )
    )
    report = usage_report(path)
    assert (report.settled, report.unavailable, report.usage) == (1, 1, None)


def test_usage_attributes_tokens_per_account(tmp_path: Path) -> None:
    path = tmp_path / "usage.jsonl"
    lines = [
        {"type": "native_settled", "accountRef": "a", "usage": {"input": 1, "output": 1}},
        {"type": "native_settled", "accountRef": "b", "usage": {"input": 2}},
        {"type": "native_settled", "accountRef": "a", "usage": {"output": 5}},
    ]
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    report = usage_report(path)
    assert report.accounts == {"a": {"input": 1, "output": 6}, "b": {"input": 2}}
    assert report.usage == {"input": 3, "output": 6}
