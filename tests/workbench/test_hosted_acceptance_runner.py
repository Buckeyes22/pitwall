"""Hosted and native-hosted acceptance runners against a fake ``pi`` (no model, no network).

The fake never edits a file, so the identity gate can pass while the coding and child gates fail.
The cases pin gate ordering, credential redaction, and the report files each runner writes.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from pitwall.workbench.hosted.acceptance import run_hosted_acceptance
from pitwall.workbench.hosted.native_acceptance import run_hosted_native_acceptance

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")

SECRET = "memory-only-account-secret"  # pragma: allowlist secret
FAKE_PI = """
let buf = '';
process.stdin.on('data', chunk => { buf += chunk; let i; while ((i = buf.indexOf('\\n')) >= 0) { const line = buf.slice(0, i); buf = buf.slice(i + 1); if (!line) continue; const c = JSON.parse(line); const emit = (x) => process.stdout.write(JSON.stringify(x) + '\\n');
const ok = (data) => emit({ type: 'response', id: c.id, command: c.type, success: true, data });
if (c.type === 'get_state') ok({ model: { provider: 'hosted-fixture', id: __MODEL__ }, isStreaming: false, pendingMessageCount: 0 });
else if (c.type === 'prompt') { ok(undefined); emit({ type: 'agent_start' }); emit({ type: 'message_end', message: { role: 'assistant', stopReason: 'stop', content: [{ type: 'text', text: 'fake answer' }] } }); emit({ type: 'agent_settled' }); }
else ok({});
}});
process.stdin.on('end', () => process.exit(0));
"""
PROFILE = {
    "provider": "hosted-fixture",
    "modelId": "hosted-fixture-model-v1",
    "endpoint": "http://127.0.0.1:1/v1",
    "api": "openai-completions",
    "apiKeyEnv": "PITWALL_PI_HOSTED_KEY",  # pragma: allowlist secret
    "accountRef": "fixture-account",
    "accountGroup": "fixture-account-group",
    "accountMaxConcurrent": 1,
    "accountInFlightTokenBudget": 10_000,
    "accountUnknownUsage": "hold",
    "servedContextTokens": 32_768,
    "maxCompletionTokens": 4_096,
    "reasoningLevel": "off",
    "resourceGroup": "hosted-fixture-resource",
    "allowProviderFallback": False,
}


def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model: str) -> tuple[Path, Path]:
    fake = tmp_path / "fake-pi.mjs"
    fake.write_text(FAKE_PI.replace("__MODEL__", json.dumps(model)))
    config = tmp_path / "profiles.json"
    config.write_text(json.dumps({"schemaVersion": 1, "profiles": {"fixture-plan": PROFILE}}))
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"fixture-account": {"type": "api", "key": SECRET}}))
    extension = tmp_path / "tintin-index.js"
    extension.write_text("export default function () {}\n")
    monkeypatch.setenv("PITWALL_WORKBENCH_PI_BIN", str(fake))
    monkeypatch.setenv("PITWALL_WORKBENCH_TINTIN_EXTENSION", str(extension))
    monkeypatch.setenv("PITWALL_WORKBENCH_RUNTIME_DIR", str(tmp_path / "runtime"))
    return config, auth


def every_file_text(root: Path) -> str:
    return "\n".join(path.read_text(errors="replace") for path in root.rglob("*") if path.is_file())


def test_wrong_model_fails_the_identity_gate_and_never_writes_the_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, auth = setup(tmp_path, monkeypatch, "another-model")
    output = tmp_path / "out"
    assert run_hosted_acceptance(config, auth, output, ["fixture-plan"]) == 1
    report = json.loads((output / "fixture-plan.json").read_text())
    assert report["status"] == "failed"
    assert report["gates"]["identity"]["status"] == "failed"
    assert report["gates"]["identity"]["actual"] == "selected provider/model differs from profile"
    assert [report["gates"][gate]["status"] for gate in ("coding", "compaction", "task_state")] == [
        "unexecuted",
        "unexecuted",
        "unexecuted",
    ]
    assert report["gates"]["accounting"]["status"] == "failed"
    assert report["childExited"] is True
    assert report["termination"]["forced"] is False
    index = json.loads((output / "index.json").read_text())
    assert index["secretValuesPrinted"] is False
    assert index["results"] == [
        {
            "profile": "fixture-plan",
            "status": "failed",
            "reportPath": str(output / "fixture-plan.json"),
        }
    ]
    assert SECRET not in every_file_text(output)
    assert (output / "index.json").stat().st_mode & 0o777 == 0o600


def test_a_model_that_does_not_repair_the_fixture_fails_the_coding_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, auth = setup(tmp_path, monkeypatch, PROFILE["modelId"])
    output = tmp_path / "out"
    assert run_hosted_acceptance(config, auth, output, ["fixture-plan"]) == 1
    gates: dict[str, Any] = json.loads((output / "fixture-plan.json").read_text())["gates"]
    assert gates["identity"]["status"] == "passed"
    assert gates["coding"]["status"] == "failed"
    assert gates["coding"]["actual"] == "independent acceptance check after repair failed"
    assert gates["compaction"]["status"] == "unexecuted"
    assert SECRET not in every_file_text(output)


def test_the_runner_refuses_a_profile_without_the_selected_account_credential_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, auth = setup(tmp_path, monkeypatch, PROFILE["modelId"])
    profiles = json.loads(config.read_text())
    profiles["profiles"]["fixture-plan"]["apiKeyEnv"] = "OTHER_KEY"  # pragma: allowlist secret
    config.write_text(json.dumps(profiles))
    with pytest.raises(ValueError, match="must use the selected hosted account reference"):
        run_hosted_acceptance(config, auth, tmp_path / "out", ["fixture-plan"])


def test_completion_token_override_must_be_an_integer_in_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, auth = setup(tmp_path, monkeypatch, PROFILE["modelId"])
    for value in ("many", "100", "9000"):
        monkeypatch.setenv("PITWALL_WORKBENCH_HOSTED_MAX_COMPLETION_TOKENS", value)
        with pytest.raises(ValueError, match="from 256 through 8192"):
            run_hosted_acceptance(config, auth, tmp_path / "out", ["fixture-plan"])


def test_native_run_without_a_delegated_child_fails_the_child_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, auth = setup(tmp_path, monkeypatch, PROFILE["modelId"])
    output = tmp_path / "out"
    assert run_hosted_native_acceptance(config, auth, output, "fixture-plan") == 1
    report = json.loads((output / "fixture-plan.json").read_text())
    assert report["status"] == "failed"
    assert report["gates"]["identity"]["status"] == "passed"
    child = report["gates"]["child"]
    assert child["status"] == "failed"
    assert child["actual"]["taskRecordFound"] is False
    assert child["actual"]["parentToolNames"] == []
    assert report["gates"]["accounting"]["status"] == "failed"
    assert report["gates"]["termination"]["status"] == "passed"
    assert report["termination"] == {"forced": False, "childExited": True}
    assert SECRET not in every_file_text(output)


def test_native_run_requires_the_pinned_tintin_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, auth = setup(tmp_path, monkeypatch, PROFILE["modelId"])
    monkeypatch.setenv("PITWALL_WORKBENCH_TINTIN_EXTENSION", str(tmp_path / "missing.js"))
    with pytest.raises(ValueError, match="@tintinweb/pi-subagents backend is not installed"):
        run_hosted_native_acceptance(config, auth, tmp_path / "out", "fixture-plan")
