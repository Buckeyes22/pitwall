"""A-48: audit inputs are derived from code and behaviour, never typed in as ``True``."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from pitwall.audit import _probes, checks
from pitwall.audit._runtime_config import RuntimeAuditConfig
from pitwall.runpod_client import pods

_AUDIT_DIR = Path(checks.__file__).parent
_RUNTIME_CONFIG = _AUDIT_DIR / "_runtime_config.py"


def test_runtime_config_inputs_are_derived(monkeypatch: pytest.MonkeyPatch) -> None:
    tree = ast.parse(_RUNTIME_CONFIG.read_text(encoding="utf-8"))
    literal_trues = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and node.value is True
    ]
    assert literal_trues == [], f"hardcoded True at lines {literal_trues}"

    cfg = RuntimeAuditConfig()
    assert cfg.probe_config()["ssh_first"] is True
    assert cfg.template_config() == {
        "cache_enabled": True,
        "create_on_cache_miss": True,
        "reuse_on_cache_hit": True,
    }
    assert cfg.terminate_config() == {"treat_404_as_success": True}
    assert cfg.webhook_config() == {"idempotent": True, "fast_200": True}
    assert cfg.kill_switch_config()["atomic"] is True

    # A regression in the audited code flips the input, so the check would fail.
    monkeypatch.setattr(pods, "POD_READINESS_PROBE_ORDER", ("runpod_proxy", "ssh_localhost"))
    assert cfg.probe_config()["ssh_first"] is False
    assert cfg.probe_config()["primary_probe"] == "runpod_proxy"

    monkeypatch.setattr(_probes, "terminate_treats_404_as_success", lambda: False)
    assert cfg.terminate_config() == {"treat_404_as_success": False}
    with pytest.raises(checks.CheckFailed):
        checks.check_15_terminate_idempotent(cfg)


def test_checks_do_not_search_source_text() -> None:
    offenders = [
        path.name
        for path in sorted(_AUDIT_DIR.glob("*.py"))
        if path.name != "_introspect.py" and "getsource" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_no_check_module_exceeds_five_hundred_lines() -> None:
    line_counts = {
        path.name: len(path.read_text(encoding="utf-8").splitlines())
        for path in _AUDIT_DIR.glob("*.py")
        if path.name != "capability.py"
    }
    assert {name: n for name, n in line_counts.items() if n > 500} == {}


def test_checks_do_not_monkeypatch_pods() -> None:
    for path in _AUDIT_DIR.glob("*.py"):
        assert "pods._rest_request =" not in path.read_text(encoding="utf-8")
