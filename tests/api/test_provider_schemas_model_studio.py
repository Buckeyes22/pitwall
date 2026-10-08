import pytest

from pitwall.api.provider_schemas import _validate_url_config
from pitwall.core.enums import ProviderType

GOOD = {
    "model_studio": {"plan": "token-plan-personal", "tier": "pro", "model": "qwen3.8-flash"},
    "openai_base_url": "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
}


def test_derived_url_is_accepted() -> None:
    _validate_url_config(ProviderType.MODEL_STUDIO, None, GOOD)


def test_redirected_url_is_refused() -> None:
    with pytest.raises(ValueError, match="openai_base_url"):
        _validate_url_config(
            ProviderType.MODEL_STUDIO, None, {**GOOD, "openai_base_url": "https://evil.example/v1"}
        )


def test_invalid_settings_are_refused() -> None:
    with pytest.raises(ValueError, match="region_not_allowed"):
        _validate_url_config(
            ProviderType.MODEL_STUDIO,
            None,
            {**GOOD, "model_studio": {**GOOD["model_studio"], "region": "us-east-1"}},
        )
