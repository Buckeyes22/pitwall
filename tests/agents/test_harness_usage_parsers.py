import json
from pathlib import Path

import pytest

from pitwall.agents.harnesses import get_adapter

FIX = Path(__file__).parent / "fixtures" / "harness_usage"


def parsed(harness: str, name: str):
    return get_adapter(harness).parse_output((FIX / name).read_bytes())


@pytest.mark.parametrize(
    "harness,name,answer,inp,out,cache_r,cache_w,reason,total,cost",
    [
        ("claude", "claude.json", b"ok\n", 2, 4, 0, 42123, None, 42129, 0.008426800000000002),
        ("mcode", "mcode.json", b"ok\n", 16270, 3, 190, None, None, 16273, None),
        ("codex", "codex.jsonl", b"ok\n", 6202, 5, 12288, 0, 0, 18495, None),
        ("opencode", "opencode.jsonl", b"ok\n", 54215, 3, 1943, 0, 0, 56161, 0),
        ("grok", "grok.json", b"ok\n", 29530, 34, 1152, 0, 33, 30716, 0.05984),
        ("pi", "pi.jsonl", b"ok\n", 2426, 2, 0, 0, 0, 2428, 0),
    ],
)
def test_parser_reads_answer_and_usage(
    harness, name, answer, inp, out, cache_r, cache_w, reason, total, cost
):
    result = parsed(harness, name)
    assert result.answer == answer
    u = result.usage
    assert u is not None and u.complete
    assert (
        u.input_tokens,
        u.output_tokens,
        u.cache_read_tokens,
        u.cache_write_tokens,
        u.reasoning_tokens,
        u.total_tokens,
        u.cost_usd,
    ) == (inp, out, cache_r, cache_w, reason, total, cost)


def test_qwen_error_result_still_reports_zero_usage_and_its_text() -> None:
    result = parsed("qwen", "qwen.json")
    assert result.answer == b"[API Error: 405 status code (no body)]\n"
    assert (result.usage.input_tokens, result.usage.output_tokens) == (0, 0)


def test_agy_error_document_becomes_a_diagnostic() -> None:
    result = parsed("agy", "agy-error.json")
    assert result.answer == b""
    assert result.diagnostic == "agy reported ERROR: authentication failed or timed out"
    assert result.usage is not None and result.usage.total_tokens == 0


def test_hermes_usage_file() -> None:
    u = get_adapter("hermes").parse_usage_file(json.loads((FIX / "hermes-usage.json").read_text()))
    assert (
        u.input_tokens,
        u.output_tokens,
        u.cache_read_tokens,
        u.cache_write_tokens,
        u.reasoning_tokens,
        u.total_tokens,
        u.cost_usd,
        u.source,
    ) == (1200, 34, 800, 0, 12, 1234, 0.0123, "harness-report")


@pytest.mark.parametrize(
    "harness", ["claude", "codex", "opencode", "grok", "pi", "qwen", "agy", "mcode"]
)
def test_garbage_is_not_usage(harness: str) -> None:
    result = get_adapter(harness).parse_output(b"Traceback (most recent call last):\n  boom\n")
    assert result.usage is None and result.answer == b""


def test_codex_partial_stream_counts_completed_turns() -> None:
    raw = (FIX / "codex.jsonl").read_bytes() + b'{"type":"turn.started"}\n'
    u = get_adapter("codex").parse_output(raw).usage
    assert u.total_tokens == 18495 and u.complete is False


def test_opencode_sums_every_step() -> None:
    line = next(
        line
        for line in (FIX / "opencode.jsonl").read_bytes().splitlines()
        if b"step_finish" in line
    )
    raw = (FIX / "opencode.jsonl").read_bytes() + line + b"\n"
    assert get_adapter("opencode").parse_output(raw).usage.total_tokens == 2 * 56161


@pytest.mark.parametrize(
    "harness", ["claude", "codex", "opencode", "grok", "pi", "qwen", "agy", "mcode"]
)
def test_json_output_mode(harness: str) -> None:
    assert get_adapter(harness).output_mode == "json"


def test_hermes_usage_file_mode() -> None:
    assert get_adapter("hermes").output_mode == "usage-file"


@pytest.mark.parametrize("harness,name", [("claude", "claude.json"), ("qwen", "qwen.json")])
def test_array_parsers_use_last_result(harness: str, name: str) -> None:
    data = json.loads((FIX / name).read_bytes())
    last = dict(data[-1], result="final")
    result = get_adapter(harness).parse_output(json.dumps([*data, last]).encode())
    assert result.answer == b"final\n"
    last["result"] = ""
    assert get_adapter(harness).parse_output(json.dumps([last]).encode()).answer == b""
    assert get_adapter(harness).parse_output(b'[{"type":"system"}]').usage is None


@pytest.mark.parametrize(
    "harness", ["claude", "codex", "opencode", "grok", "pi", "qwen", "agy", "mcode"]
)
@pytest.mark.parametrize(
    "raw",
    [
        b"null",
        b"[]",
        b'{"usage":null}',
        b'{"type":"turn.completed","usage":{}}',
        b'{"type":"step_finish","part":null}',
        b'{"type":"turn_end","message":null}',
    ],
)
def test_unrecognized_json_is_not_zero_usage(harness: str, raw: bytes) -> None:
    assert get_adapter(harness).parse_output(raw).usage is None


def test_opencode_partial_stream_retains_completed_steps() -> None:
    raw = (FIX / "opencode.jsonl").read_bytes() + b'{"type":"step_start"}\n{"type":'
    usage = get_adapter("opencode").parse_output(raw).usage
    assert usage is not None and usage.total_tokens == 56161 and not usage.complete


def test_pi_partial_stream_counts_turns_once() -> None:
    lines = (FIX / "pi.jsonl").read_bytes().splitlines()
    raw = b"\n".join(line for line in lines if b'"type":"agent_end"' not in line)
    usage = get_adapter("pi").parse_output(raw).usage
    assert usage is not None and usage.total_tokens == 2428 and not usage.complete
    turn = next(line for line in lines if b'"type":"turn_end"' in line)
    usage = get_adapter("pi").parse_output(raw + b"\n" + turn).usage
    assert usage is not None and usage.total_tokens == 4856


def test_codex_sums_turns_and_uses_last_answer() -> None:
    raw = (FIX / "codex.jsonl").read_bytes() * 2
    raw += b'{"type":"item.completed","item":{"type":"agent_message","text":"final"}}\n'
    result = get_adapter("codex").parse_output(raw)
    assert result.answer == b"final\n"
    assert result.usage is not None and result.usage.total_tokens == 36990


def test_hermes_failed_report_is_incomplete() -> None:
    data = json.loads((FIX / "hermes-usage.json").read_bytes())
    data["failed"] = True
    usage = get_adapter("hermes").parse_usage_file(data)
    assert usage is not None and not usage.complete
    for invalid in (None, [], {}, {"input_tokens": True, "output_tokens": -1}):
        assert get_adapter("hermes").parse_usage_file(invalid) is None


@pytest.mark.parametrize(
    "harness,flag",
    [
        ("claude", "--output-format"),
        ("codex", "--json"),
        ("opencode", "--format"),
        ("grok", "--output-format"),
        ("pi", "--mode"),
        ("qwen", "--output-format"),
        ("agy", "--output-format"),
        ("hermes", "--usage-file"),
        ("mcode", "--output-format"),
    ],
)
@pytest.mark.parametrize("suffix", ["", "=text"])
def test_output_flags_are_managed(harness: str, flag: str, suffix: str, tmp_path: Path) -> None:
    from pitwall.agents.errors import UsageError

    args = ["provider/model", "prompt"] if harness == "opencode" else ["prompt"]
    with pytest.raises(UsageError, match="managed by the shim"):
        get_adapter(harness).parse([*args, flag + suffix], {}, tmp_path)


def test_qwen_absent_cache_write_and_cost_stay_unknown() -> None:
    usage = parsed("qwen", "qwen.json").usage
    assert usage is not None and usage.total_tokens == 0
    assert usage.cache_write_tokens is None and usage.cost_usd is None


def test_agy_success_maps_answer_and_thinking_tokens() -> None:
    document = json.loads((FIX / "agy-error.json").read_bytes())
    document.update(status="SUCCESS", response="answer")
    document["usage"].update(
        input_tokens=10, output_tokens=2, thinking_tokens=1, cache_read_tokens=3, total_tokens=15
    )
    result = get_adapter("agy").parse_output(json.dumps(document).encode())
    assert result.answer == b"answer\n" and result.diagnostic is None
    assert result.usage is not None
    assert (
        result.usage.input_tokens,
        result.usage.output_tokens,
        result.usage.reasoning_tokens,
        result.usage.cache_read_tokens,
        result.usage.total_tokens,
    ) == (10, 2, 1, 3, 15)


def test_opencode_text_parts_preserve_order() -> None:
    raw = (FIX / "opencode.jsonl").read_bytes()
    raw += b'{"type":"text","part":{"text":"next"}}\n'
    assert get_adapter("opencode").parse_output(raw).answer == b"ok\nnext\n"


def test_pi_supports_usage_directly_on_turn_end() -> None:
    raw = b'{"type":"turn_end","usage":{"input":3,"output":1,"totalTokens":4}}\n{"type":"agent_end"}\n'
    result = get_adapter("pi").parse_output(raw)
    assert result.answer == b""
    assert result.usage is not None and result.usage.total_tokens == 4 and result.usage.complete


@pytest.mark.parametrize(
    "harness,name,answer",
    [
        ("claude", "claude.json", b"ok\n"),
        ("codex", "codex.jsonl", b"ok\n"),
        ("opencode", "opencode.jsonl", b"ok\n"),
        ("grok", "grok.json", b"ok\n"),
        ("pi", "pi.jsonl", b"ok\n"),
        ("qwen", "qwen.json", b"[API Error: 405 status code (no body)]\n"),
        ("agy", "agy-error.json", b""),
        ("mcode", "mcode.json", b"ok\n"),
    ],
)
def test_dispatch_renders_captured_json_as_plain_answer(
    harness: str, name: str, answer: bytes
) -> None:
    from tests.agents.shim_test_support import ShimSandbox

    sandbox = ShimSandbox()
    try:
        sandbox.install_harness(harness)
        raw = (FIX / name).read_bytes()
        args = [str(sandbox.prompt())]
        if harness == "opencode":
            args.insert(0, "provider/model")
        completed = sandbox.run(harness, args, env=sandbox.environment(FAKE_STDOUT=raw.decode()))
        assert completed.returncode == 0, completed.stderr
        (run,) = sandbox.run_directories()
        assert (run / "stdout.log").read_bytes() == answer
        assert (run / "harness-output.jsonl").read_bytes() == raw
        assert raw not in completed.stdout
        assert completed.stdout == answer + b"\nSHIM-DONE exit=0\n"
        result = json.loads((run / "result.json").read_bytes())
        assert result["usage"] is not None and result["usage"]["complete"]
        assert sandbox.ledger_records()[-1]["usage"] == result["usage"]
    finally:
        sandbox.cleanup()


def test_mcode_failed_result_retains_incomplete_usage() -> None:
    document = json.loads((FIX / "mcode.json").read_bytes())
    document["status"] = "failed"
    result = get_adapter("mcode").parse_output(json.dumps(document).encode())
    assert result.answer == b"ok\n"
    assert result.usage is not None and not result.usage.complete
    assert result.usage.total_tokens == 16273


@pytest.mark.parametrize("data", [None, {}, {"inputTokens": True, "outputTokens": -1}])
def test_mcode_missing_or_invalid_usage_stays_unknown(data: object) -> None:
    document = json.loads((FIX / "mcode.json").read_bytes())
    document["usage"] = data
    result = get_adapter("mcode").parse_output(json.dumps(document).encode())
    assert result.answer == b"ok\n" and result.usage is None
