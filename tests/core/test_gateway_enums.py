from pitwall.core.enums import ProviderAdapterId, ProviderType
from pitwall.core.models import default_credential_reference


def test_gateway_enum_members_exist() -> None:
    assert ProviderType.OPENAI_GATEWAY == "openai_gateway"
    assert ProviderAdapterId.GATEWAY == "openai_gateway"


def test_gateway_default_credential_reference_is_optional_key_env() -> None:
    assert default_credential_reference(ProviderAdapterId.GATEWAY) == "PITWALL_GATEWAY_API_KEY"
