"""Config CLI failure exits/output exercised through the real settings loader."""

import json
import os

import pytest

from pitwall import cli


@pytest.mark.parametrize("json_output", [False, True])
@pytest.mark.parametrize(
    "failure", ["missing_file", "malformed_toml", "invalid_value", "missing_required"]
)
def test_config_failure_is_actionable_and_never_echoes_values(
    monkeypatch, tmp_path, capsys, failure, json_output
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PITWALL_CONFIG_FILE", raising=False)
    canary = "config-private-value-canary"
    if failure == "missing_file":
        monkeypatch.setenv("PITWALL_CONFIG_FILE", str(tmp_path / "missing.toml"))
    elif failure == "malformed_toml":
        path = tmp_path / "broken.toml"
        path.write_text('bad = "' + canary)
        monkeypatch.setenv("PITWALL_CONFIG_FILE", str(path))
    elif failure == "invalid_value":
        monkeypatch.setenv("PITWALL_ROUTING_MODE", canary)
    else:
        monkeypatch.setenv("RUNPOD_API_KEY", "")
    # The API boots without a RunPod key; the MCP server still requires it.
    service = "mcp" if failure == "missing_required" else "api"
    argv = ["config", "check", service] + (["--json"] if json_output else [])
    assert cli.main(argv) == os.EX_CONFIG
    output = capsys.readouterr()
    assert canary not in output.out + output.err
    assert "Traceback" not in output.out + output.err
    if json_output:
        data = json.loads(output.out)
        assert data["error"] and output.err == ""
        if failure == "missing_required":
            assert "RUNPOD_API_KEY" in data["error"] and data["errors"]
    else:
        assert output.err and output.out == ""
        if failure == "missing_required":
            assert "RUNPOD_API_KEY" in output.err
