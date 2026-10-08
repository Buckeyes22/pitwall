"""Count-sync gate — docs must state the same tool count as TOOL_NAMES (J10 refresh §12.4).

Hermetic: reads only checked-in markdown files and compares to the canonical
registry size. Fails if any documented total drifts from len(TOOL_NAMES) or
if the J10 journey still claims the stale "≥ 20 tools" threshold.
"""

from __future__ import annotations

import re
from pathlib import Path

from pitwall.mcp.registry import TOOL_NAMES

EXPECTED = len(TOOL_NAMES)
REPO_ROOT = Path(__file__).resolve().parents[2]
# Fallback: walk up until docs/ found (covers layout changes)
if not (REPO_ROOT / "docs").exists():
    cur = Path(__file__).resolve().parent
    for cand in [cur, *cur.parents]:
        if (cand / "docs").exists() and (cand / "src").exists():
            REPO_ROOT = cand
            break

SDLC = REPO_ROOT / "docs" / "sdlc" / "03-mcp-server.md"
SUPPORT = REPO_ROOT / "docs" / "support-matrix.md"
JOURNEYS = REPO_ROOT / "docs" / "operator" / "user-journey-catalog.md"


def _read(path: Path) -> str:
    assert path.exists(), f"missing doc file: {path}"
    return path.read_text(encoding="utf-8")


def test_sdlc_doc_count_matches_registry() -> None:
    text = _read(SDLC)
    expected = EXPECTED

    # Collect every plausible total-count claim in this doc.
    # Total counts are introduced with distinctive phrasing; sub-group
    # counts like "27 tools" / "29 tools" / "49 tools" use different
    # phrasing and must not be treated as the global total.
    patterns = {
        "exposes N named tools": r"exposes\s+(\d+)\s+named tools",
        "registers all N tools": r"registers all\s+(\d+)\s+tools",
        "All N tools are registered": r"All\s+(\d+)\s+tools\s+are registered",
        "frozenset (N names)": r"frozenset\[str\].*?\(\s*(\d+)\s+names\s*\)",
        "remain exactly N": r"remain exactly\s+(\d+)",
        "len(TOOL_REGISTRY) == N": r"len\(TOOL_REGISTRY\)\s*==\s*(\d+)",
        "N statically registered tools (if present)": r"(\d+)\s+statically registered tools",
    }

    found: list[tuple[str, int]] = []
    for label, pat in patterns.items():
        for m in re.finditer(pat, text):
            found.append((label, int(m.group(1))))

    # We expect the canonical doc to contain at least the core claims.
    assert found, (
        f"No total-count claims found in {SDLC}; expected patterns like 'exposes 76 named tools'"
    )
    for label, n in found:
        assert n == expected, (
            f"{SDLC} [{label}] claims {n} but TOOL_NAMES has {expected} "
            f"(stale doc count — update to {expected})"
        )

    # Explicit stale-count guard: the previous total was 75 (Task 1 bumped to 76).
    # If any stale literal remains, fail fast.
    for stale in (expected - 1, expected + 1):
        # Only flag stale numbers that appear in a total-count context to avoid
        # flagging unrelated numbers (e.g. line counts, port numbers).
        totalish = re.findall(rf"\b{stale}\b[^\\n]*?tools", text)
        # Filter to total-ish phrasing; ignore known sub-group listings
        totalish = [s for s in totalish if re.search(r"named|statically|All\s+", s)]
        assert not totalish, f"{SDLC} still mentions stale count {stale}: {totalish!r}"


def test_support_matrix_count_matches_registry() -> None:
    text = _read(SUPPORT)
    expected = EXPECTED

    m = re.search(r"(\d+)\s+statically registered tools", text)
    assert m is not None, (
        f"{SUPPORT} missing 'N statically registered tools' claim (expected '{expected} statically registered tools')"
    )
    doc_count = int(m.group(1))
    assert doc_count == expected, (
        f"{SUPPORT} claims {doc_count} statically registered tools but TOOL_NAMES has {expected}"
    )
    # Ensure no other total claim contradicts (there should be exactly one)
    all_counts = [int(x) for x in re.findall(r"(\d+)\s+statically registered tools", text)]
    for c in all_counts:
        assert c == expected, f"{SUPPORT} has inconsistent counts {all_counts} vs {expected}"


def test_j10_tool_count_matches_registry() -> None:
    text = _read(JOURNEYS)
    expected = EXPECTED

    # Find the J10 row explicitly — it is the MCP journey.
    j10_lines = [line for line in text.splitlines() if re.search(r"\|\s*J10\s*\|", line)]
    assert j10_lines, f"No '| J10 |' row found in {JOURNEYS}"
    j10_block = "\n".join(j10_lines)

    # Stale hard-coded threshold must not remain anywhere in the file
    assert "≥ 20 tools" not in text, (
        f"{JOURNEYS} still contains stale '≥ 20 tools' — J10 must claim {expected} tools"
    )
    assert ">= 20 tools" not in text, (
        f"{JOURNEYS} still contains stale '>= 20 tools' — J10 must claim {expected} tools"
    )

    # Extract the tool count(s) claimed in the J10 row(s)
    counts = [int(x) for x in re.findall(r"(\d+)\s+tools", j10_block)]
    assert counts, f"J10 row in {JOURNEYS} does not state a '<N> tools' count: {j10_block!r}"
    for c in counts:
        assert c == expected, (
            f"J10 row claims {c} tools but TOOL_NAMES has {expected}: {j10_block!r} — "
            f"update J10 to '{expected} tools'"
        )

    # Also ensure no row still advertises the old threshold via
    # alternate phrasing like "≥ 20" even without "tools"
    assert "≥ 20" not in j10_block or str(expected) in j10_block, (
        f"J10 row still advertises stale '≥ 20': {j10_block!r}"
    )
