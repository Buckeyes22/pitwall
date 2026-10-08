"""Workflow policy: jobs that sync the root project provision the pinned interpreter."""

from __future__ import annotations

from tools.ci.check_workflows import WORKFLOWS, unprovisioned_uv_sync_jobs

_SYNC_WITHOUT_PYTHON = """
jobs:
  smoke:
    steps:
      - uses: astral-sh/setup-uv@0123
      - run: uv sync --frozen --extra dev
"""

_PROVISIONED = """
jobs:
  a:
    steps:
      - uses: actions/setup-python@0123
      - run: uv sync --frozen
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
  d:
    steps:
      - run: echo no sync
"""


def test_a_job_that_syncs_without_provisioning_python_is_reported() -> None:
    assert unprovisioned_uv_sync_jobs(_SYNC_WITHOUT_PYTHON) == ["smoke"]


def test_every_provisioning_form_is_accepted() -> None:
    assert unprovisioned_uv_sync_jobs(_PROVISIONED) == []


def test_the_repository_ci_workflow_provisions_python_everywhere_it_syncs() -> None:
    assert unprovisioned_uv_sync_jobs((WORKFLOWS / "ci.yml").read_text(encoding="utf-8")) == []
