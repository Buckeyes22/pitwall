from __future__ import annotations

from pitwall.agents.session_usage import summarize


def row(
    session: str,
    total: int,
    *,
    shim: str = "codex",
    model: str = "m",
    usage: bool = True,
    ts: str = "2026-10-15T10:00:00Z",
) -> dict[str, object]:
    result: dict[str, object] = {
        "event": "finished",
        "session": session,
        "shim": shim,
        "model": model,
        "ts": ts,
        "dispatch_id": f"{session}-{total}-{ts}",
    }
    if usage:
        result["usage"] = {
            "inputTokens": total - 1,
            "outputTokens": 1,
            "cacheReadTokens": None,
            "cacheWriteTokens": None,
            "reasoningTokens": None,
            "totalTokens": total,
            "costUsd": None,
            "complete": True,
            "source": "harness-json",
        }
    else:
        result["usageError"] = "codex reports no token usage"
    return result


def test_two_sessions_are_reported_separately() -> None:
    totals = {item.session: item for item in summarize([row("a", 10), row("b", 5), row("a", 7)])}
    assert (totals["a"].usage.total_tokens, totals["b"].usage.total_tokens) == (17, 5)


def test_runs_without_usage_are_counted_not_zeroed() -> None:
    [totals] = summarize([row("a", 10), row("a", 0, usage=False)])
    assert (
        totals.dispatches,
        totals.with_usage,
        totals.without_usage,
        totals.usage.total_tokens,
    ) == (2, 1, 1, 10)


def test_filters_by_session_and_since() -> None:
    rows = [
        row("a", 10, ts="2026-10-01T00:00:00Z"),
        row("a", 3, ts="2026-10-15T00:00:00Z"),
        row("b", 1),
    ]
    [totals] = summarize(rows, session="a", since="2026-10-10")
    assert totals.usage.total_tokens == 3


def test_rows_from_before_this_feature_are_grouped_as_unattributed() -> None:
    old = {
        "event": "finished",
        "shim": "codex",
        "model": "m",
        "ts": "2026-09-01T00:00:00Z",
        "dispatch_id": "x",
    }
    [totals] = summarize([old])
    assert (totals.session, totals.without_usage) == ("unattributed", 1)


def test_rows_without_a_finished_event_are_ignored() -> None:
    [totals] = summarize([row("a", 10), {"session": "a", "ts": "2026-10-15T11:00:00Z"}])
    assert (totals.dispatches, totals.usage.total_tokens) == (1, 10)
