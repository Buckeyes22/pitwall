from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from pitwall.config import PitwallSettings
from pitwall.core.enums import CapabilityClass, CapabilitySource, CostMode, ProviderType
from pitwall.core.models import Capability, Provider
from pitwall.providers.selfhosted.profile import self_hosted_profile
from pitwall.providers.selfhosted.readiness import oracle_for
from pitwall.serve import warm_self_hosted

pytestmark = pytest.mark.live
BASE_URL = os.environ.get("PITWALL_SELFHOSTED_BASE_URL")
KEY_ENV = os.environ.get("PITWALL_SELFHOSTED_API_KEY_ENV")
if not BASE_URL or not KEY_ENV:
    pytest.skip("self-hosted live endpoint is not configured", allow_module_level=True)
API_KEY = os.environ.get(KEY_ENV)
if not API_KEY:
    pytest.skip("the configured API-key environment variable is unset", allow_module_level=True)


def models(payload: dict[str, object]) -> list[str]:
    rows = payload.get("data", [])
    assert isinstance(rows, list)
    return [row["id"] for row in rows if isinstance(row, dict) and "id" in row]


@pytest.mark.anyio
async def test_live_selfhosted_endpoint_records_evidence() -> None:
    assert BASE_URL is not None and API_KEY is not None
    headers = {"Authorization": f"Bearer {API_KEY}"}
    timeout_s = 360
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        listed = await client.get(f"{BASE_URL.rstrip('/')}/models", headers=headers)
        listed.raise_for_status()
        model_ids = models(listed.json())
        empty_catalogue = not model_ids
        evidence: dict[str, object] = {
            "observed_at": datetime.now(UTC).isoformat(),
            "models": model_ids,
            "empty_catalogue": empty_catalogue,
        }
        if empty_catalogue:
            evidence["staged_scenario"] = "empty_catalogue"
        else:
            model_id = model_ids[0]
            provider = Provider(
                id="prov_live_selfhosted",
                capability_id="cap_live_selfhosted",
                name="live-selfhosted",
                provider_type=ProviderType.PUBLIC_ENDPOINT,
                config={
                    "base_url": BASE_URL,
                    "api_key_env": KEY_ENV,
                    "self_hosted": {
                        "readiness": {"kind": "openai-models"},
                        "cold_start_timeout_s": timeout_s,
                        "warmup": {"prompt": "ping", "max_tokens": 1},
                        "models": [{"id": model_id}],
                    },
                },
                source=CapabilitySource.API,
                updated_at=datetime.now(UTC),
            )
            capability = Capability(
                id="cap_live_selfhosted",
                name="llm.live-selfhosted",
                version="1.0.0",
                class_=CapabilityClass.LLM,
                cost_mode=CostMode.ZERO,
                source=CapabilitySource.API,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                served_model_id=model_id,
            )
            profile = self_hosted_profile(provider)
            assert profile is not None
            before = await oracle_for(profile).observe(
                client=client,
                base_url=BASE_URL,
                headers=headers,
                model_id=model_id,
            )
            outcome = await warm_self_hosted(
                provider=provider,
                profile=profile,
                capability=capability,
                client=client,
                settings=PitwallSettings(),
            )
            chat = await client.post(
                f"{BASE_URL.rstrip('/')}/chat/completions",
                headers=headers,
                json={
                    "model": model_id,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                },
            )
            chat.raise_for_status()
            evidence.update(
                readiness_before=before.state,
                readiness_after=outcome.observation.state,
                warm_elapsed_s=outcome.elapsed_s,
                survived_global_timeout=outcome.elapsed_s > 330,
                chat_status=chat.status_code,
            )
            assert evidence["survived_global_timeout"] is True
        unauthorized = await client.get(
            f"{BASE_URL.rstrip('/')}/models",
            headers={"Authorization": "Bearer invalid-live-test-token"},
        )
        evidence["unauthorized_status"] = unauthorized.status_code
        assert unauthorized.status_code == 401
    output = Path("artifacts/selfhosted/live-evidence.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8")
    assert evidence["empty_catalogue"] or evidence["readiness_after"] == "ready"
