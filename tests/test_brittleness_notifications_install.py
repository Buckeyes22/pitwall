"""The missing-resend hint tells users the supported install command for this release."""

from __future__ import annotations

import importlib
from typing import Any

import pytest

import pitwall
from pitwall.cost.notifications import ResendNotifier


def test_missing_resend_hint_uses_python_314_and_the_running_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(name: str, *args: Any, **kwargs: Any) -> Any:
        error = ModuleNotFoundError("No module named 'resend'")
        error.name = "resend"
        raise error

    monkeypatch.setattr(importlib, "import_module", refuse)

    result = ResendNotifier(api_key="k", sender="a@example.test", recipient="b@example.test").send(
        subject="s", body="b"
    )

    assert result.ok is False
    assert result.error is not None
    version = pitwall.__version__
    assert "uv tool install --python 3.14 " in result.error
    assert "3.14.7" not in result.error
    assert f"/download/v{version}/pitwall-{version}-py3-none-any.whl" in result.error
