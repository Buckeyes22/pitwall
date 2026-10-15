from pitwall.agents.harnesses.base import HarnessAdapter
from pitwall.agents.token_usage import (
    ParsedOutput,
    TokenUsage,
    as_int,
    json_document,
    json_lines,
)


def test_addition_keeps_unknowns_unknown_and_sums_knowns() -> None:
    a = TokenUsage(10, 2, cache_read_tokens=5, cost_usd=0.5)
    b = TokenUsage(1, 1, cache_read_tokens=None, cost_usd=0.25, complete=False)
    total = a + b
    assert (total.input_tokens, total.output_tokens) == (11, 3)
    assert total.cache_read_tokens == 5
    assert total.cost_usd == 0.75
    assert total.complete is False


def test_public_dict_shape() -> None:
    assert TokenUsage(1, 2, total_tokens=3).to_public_dict() == {
        "inputTokens": 1,
        "outputTokens": 2,
        "cacheReadTokens": None,
        "cacheWriteTokens": None,
        "reasoningTokens": None,
        "totalTokens": 3,
        "costUsd": None,
        "complete": True,
        "source": "harness-json",
    }


def test_public_dict_round_trip() -> None:
    usage = TokenUsage(
        11,
        2,
        cache_read_tokens=3,
        cache_write_tokens=4,
        reasoning_tokens=1,
        total_tokens=20,
        cost_usd=0.25,
        complete=False,
        source="harness-report",
    )
    assert TokenUsage.from_public_dict(usage.to_public_dict()) == usage


def test_json_helpers_tolerate_noise() -> None:
    assert json_lines(b'{"a":1}\nnot json\n\n{"b":2}\n') == [{"a": 1}, {"b": 2}]
    assert json_document(b"Reading additional input\n") is None
    assert as_int(True) is None and as_int(-1) is None and as_int(7) == 7


def test_adapter_defaults_to_passthrough_text_mode() -> None:
    adapter = HarnessAdapter()
    raw = b"plain answer\n"
    assert adapter.output_mode == "text"
    assert adapter.parse_output(raw) == ParsedOutput(raw, None)
    assert adapter.parse_usage_file({"input_tokens": 1}) is None
