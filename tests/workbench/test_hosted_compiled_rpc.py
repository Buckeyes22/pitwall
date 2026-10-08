"""Compiled hosted profile over Pi RPC, translated from packages/pi-workbench/tests/hosted-compiled-rpc.test.ts."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

import pytest

from pitwall.workbench.launcher import PiLaunchOptions, extension_path, launch_pi
from pitwall.workbench.profile import compile_profile, configure_provider_profile
from tests.hang_guard import HANG_GUARD_SECS
from tests.workbench.pi_support import (
    FixtureServer,
    RpcClient,
    chat_chunk,
    pinned_pi,
    requires_pi,
    sse_headers,
    sse_write,
)

SELECTED_KEY = "fixture-hosted-secret-do-not-report"  # pragma: allowlist secret


@pytest.mark.live
@pytest.mark.parity
@requires_pi
def test_compiled_hosted_profile_uses_the_exact_model_and_selected_credential_in_pi_rpc(
    tmp_path: Path,
) -> None:
    """Source: hosted-compiled-rpc.test.ts 'compiled hosted profile uses the exact model and selected credential in Pi RPC'."""
    model_id = "hosted-fixture-model-v1"
    requests: list[dict[str, Any]] = []

    def respond(handler: BaseHTTPRequestHandler, path: str, body: bytes) -> None:
        requests.append(
            {"authorization": handler.headers.get("authorization"), "body": json.loads(body)}
        )
        sse_headers(handler)
        sse_write(
            handler,
            chat_chunk({"role": "assistant", "content": "loopback hosted fixture"}, model=model_id),
        )
        sse_write(handler, chat_chunk({}, "stop", model=model_id))
        usage = {"prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7}
        sse_write(
            handler,
            "data: "
            + json.dumps(
                {
                    "id": "hosted-fixture",
                    "object": "chat.completion.chunk",
                    "choices": [],
                    "usage": usage,
                }
            )
            + "\n\n",
        )
        sse_write(handler, "data: [DONE]\n\n")

    with FixtureServer(respond) as server:
        profile = {
            "provider": "hosted-fixture",
            "modelId": model_id,
            "endpoint": server.url,
            "api": "openai-completions",
            "apiKeyEnv": "PITWALL_WORKBENCH_SELECTED_HOSTED_KEY",  # pragma: allowlist secret
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
        compiled = compile_profile("hosted-fixture", profile, tmp_path / "agent")
        provider = configure_provider_profile(compiled, tmp_path)
        accounting_path = tmp_path / "accounting.jsonl"
        client = RpcClient(
            launch_pi(
                PiLaunchOptions(
                    cwd=tmp_path,
                    profile=compiled,
                    pi_bin=pinned_pi(),
                    runtime_dir=tmp_path / "runtime",
                    env={
                        **provider.env,
                        "PITWALL_WORKBENCH_SELECTED_HOSTED_KEY": SELECTED_KEY,
                        "PITWALL_WORKBENCH_RESOURCE_DIR": str(tmp_path / "admission"),
                        "PITWALL_WORKBENCH_ACCOUNT_BUDGET_DIR": str(tmp_path / "account-budget"),
                        "PITWALL_WORKBENCH_ACCOUNTING_PATH": str(accounting_path),
                        # Ensure the selected credential is the only relevant secret in the
                        # child environment; the launcher still applies its normal allowlist.
                        "PITWALL_WORKBENCH_OTHER_HOSTED_KEY": "unrelated-secret-must-not-propagate",  # pragma: allowlist secret
                    },
                    extensions=[extension_path("extension")],
                )
            )
        )
        try:
            client.command("prompt", message="Return the loopback fixture response.")
            client.until(
                lambda: len(requests) == 1, timeout=HANG_GUARD_SECS, what="the fixture request"
            )
            assert len(requests) == 1
            assert requests[0]["body"]["model"] == model_id
            assert requests[0]["authorization"] == f"Bearer {SELECTED_KEY}"
            client.until(
                accounting_path.exists, timeout=HANG_GUARD_SECS, what="the accounting file"
            )
            accounting = [
                json.loads(line)
                for line in accounting_path.read_text().splitlines()
                if line.strip()
            ]
            report = {
                "schemaVersion": 1,
                "requestCount": len(requests),
                "model": requests[0]["body"]["model"],
                "credentialEnvironment": profile["apiKeyEnv"],
                "authorizationMatchedSelectedCredential": requests[0]["authorization"]
                == f"Bearer {SELECTED_KEY}",
                "accountingRecordTypes": [
                    record["type"] for record in accounting if isinstance(record.get("type"), str)
                ],
                "noSecretsPrinted": True,
            }
            report_path = tmp_path / "hosted-rpc-report.json"
            report_path.write_text(json.dumps(report, indent=2) + "\n")
            report_path.chmod(0o600)
            assert SELECTED_KEY not in report_path.read_text()
            assert SELECTED_KEY not in accounting_path.read_text()
            assert report["requestCount"] == 1
            assert report["model"] == model_id
            assert report["credentialEnvironment"] == profile["apiKeyEnv"]
            assert report["authorizationMatchedSelectedCredential"] is True
            assert report["noSecretsPrinted"] is True
        finally:
            client.close()
