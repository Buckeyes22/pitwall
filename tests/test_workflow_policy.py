"""Workflow policy: jobs that sync the root project provision the pinned interpreter."""

from __future__ import annotations

from tools.ci.check_workflows import (
    WORKFLOWS,
    check_workflow,
    setup_python_without_version_file,
    unprovisioned_uv_sync_jobs,
)

_SYNC_WITHOUT_PYTHON = """
jobs:
  smoke:
    steps:
      - uses: astral-sh/setup-uv@0123
      - run: uv sync --frozen --extra dev
"""

_SETUP_PYTHON_ONLY = """
jobs:
  a:
    steps:
      - uses: actions/setup-python@0123
      - run: uv sync --frozen
  d:
    steps:
      - run: echo no sync
"""

# uv-based provisioning cannot fetch a CPython newer than the pinned uv knows, so it is refused.
_UV_PROVISIONED = """
jobs:
  b:
    steps:
      - uses: astral-sh/setup-uv@0123
      - run: uv python install 3.14.7
      - run: uv sync --frozen
  c:
    steps:
      - uses: astral-sh/setup-uv@0123
        with:
          python-version: "3.14.7"
      - run: uv sync --frozen
"""

_VERSION_FILE = """
jobs:
  good:
    steps:
      - uses: actions/setup-python@0123
        with:
          python-version-file: .python-version
  literal:
    steps:
      - uses: actions/setup-python@0123
        with:
          python-version: "3.14.7"
  both:
    steps:
      - uses: actions/setup-python@0123
        with:
          python-version-file: .python-version
          python-version: "3.14.7"
  other-file:
    steps:
      - uses: actions/setup-python@0123
        with:
          python-version-file: pyproject.toml
"""


def test_a_job_that_syncs_without_provisioning_python_is_reported() -> None:
    assert unprovisioned_uv_sync_jobs(_SYNC_WITHOUT_PYTHON) == ["smoke"]


def test_only_setup_python_provisions_the_interpreter() -> None:
    assert unprovisioned_uv_sync_jobs(_SETUP_PYTHON_ONLY) == []
    assert unprovisioned_uv_sync_jobs(_UV_PROVISIONED) == ["b", "c"]


def test_setup_python_must_read_the_python_version_file() -> None:
    assert setup_python_without_version_file(_VERSION_FILE) == ["literal", "both", "other-file"]


def test_every_repository_workflow_passes_the_trust_policy() -> None:
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        assert check_workflow(path) == [], path.name


def test_the_repository_ci_workflow_provisions_python_everywhere_it_syncs() -> None:
    assert unprovisioned_uv_sync_jobs((WORKFLOWS / "ci.yml").read_text(encoding="utf-8")) == []
