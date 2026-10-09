"""`make test-fast` and `make test-cov` select the same tests as CI's hermetic suite.

A `-m` on the command line replaces the `-m 'not live'` in pyproject's addopts, so a selector
that lacks `and not live` would run paid live tests whenever live credentials are sourced.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SELECTOR = re.compile(r'-m "([^"]+)"')


def _ci_hermetic_selector() -> str:
    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    run = "\n".join(str(step.get("run", "")) for step in ci["jobs"]["test"]["steps"])
    selectors = [s for s in SELECTOR.findall(run) if "not integration" in s]
    assert len(selectors) == 1, selectors
    return selectors[0]


def _makefile_selector(target: str) -> str:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    recipe = re.search(rf"^{re.escape(target)}:[^\n]*", makefile, flags=re.M)
    assert recipe is not None, f"Makefile has no {target} target"
    selectors = SELECTOR.findall(recipe.group(0))
    assert len(selectors) == 1, (target, selectors)
    return selectors[0]


@pytest.mark.parametrize("target", ["test-fast", "test-cov"])
def test_makefile_selector_equals_the_ci_test_job_selector(target: str) -> None:
    assert _makefile_selector(target) == _ci_hermetic_selector()


def test_the_selector_excludes_live_tests() -> None:
    assert _ci_hermetic_selector().endswith("and not live")
