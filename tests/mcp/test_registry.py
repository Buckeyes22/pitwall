"""Tests for the MCP tool registry table."""

from __future__ import annotations

import pytest

from pitwall.mcp.registry import TOOL_NAMES, TOOL_REGISTRY, register_all
from tests.mcp.test_registry_health import EXPECTED_TOOL_COUNT


class TestToolRegistryStructure:
    def test_registry_contains_exactly_the_expected_tools(self) -> None:
        assert len(TOOL_NAMES) == EXPECTED_TOOL_COUNT
        assert len(TOOL_REGISTRY) == EXPECTED_TOOL_COUNT
        assert {"pitwall_models_list", "pitwall_models_fit"} <= TOOL_NAMES
        assert {
            "pitwall_preview_route",
            "pitwall_get_job_events",
            "pitwall_runpod_onboarding_plan",
            "pitwall_runpod_onboarding_apply",
            "pitwall_runpod_onboarding_status",
            "pitwall_runpod_onboarding_resume",
            "pitwall_runpod_onboarding_rollback",
        } <= TOOL_NAMES

    def test_all_names_start_with_pitwall_prefix(self) -> None:
        for spec in TOOL_REGISTRY:
            assert spec.name.startswith("pitwall_"), f"{spec.name} lacks pitwall_ prefix"

    def test_all_names_are_in_tool_names_set(self) -> None:
        for spec in TOOL_REGISTRY:
            assert spec.name in TOOL_NAMES

    def test_no_duplicate_names(self) -> None:
        names = [spec.name for spec in TOOL_REGISTRY]
        assert len(names) == len(set(names))

    def test_every_spec_has_nonempty_description(self) -> None:
        for spec in TOOL_REGISTRY:
            assert spec.description, f"{spec.name} has empty description"

    def test_every_spec_has_callable_handler(self) -> None:
        for spec in TOOL_REGISTRY:
            assert callable(spec.handler), f"{spec.name} handler is not callable"

    def test_tool_spec_is_frozen(self) -> None:
        spec = TOOL_REGISTRY[0]
        with pytest.raises(AttributeError):
            spec.name = "x"


class TestToolRegistryDiscoveryGroup:
    NAMES = {
        "pitwall_list_capabilities",
        "pitwall_describe_capability",
        "pitwall_list_providers",
        "pitwall_get_provider_health",
    }

    def test_discovery_tools_present(self) -> None:
        assert self.NAMES.issubset(TOOL_NAMES)


class TestToolRegistryInferenceGroup:
    NAMES = {
        "pitwall_submit_inference",
        "pitwall_submit_job",
        "pitwall_get_job_status",
        "pitwall_get_job_result",
        "pitwall_cancel_job",
    }

    def test_inference_tools_present(self) -> None:
        assert self.NAMES.issubset(TOOL_NAMES)


class TestToolRegistryLeaseGroup:
    NAMES = {
        "pitwall_lease_pod",
        "pitwall_get_lease",
        "pitwall_renew_lease",
        "pitwall_stop_lease",
    }

    def test_lease_tools_present(self) -> None:
        assert self.NAMES.issubset(TOOL_NAMES)


class TestToolRegistryServeGroup:
    def test_serve_model_tool_present(self) -> None:
        assert "pitwall_serve_model" in TOOL_NAMES
        spec = next(item for item in TOOL_REGISTRY if item.name == "pitwall_serve_model")
        assert spec.handler.__name__ == "pitwall_serve_model"


class TestToolRegistryModelsGroup:
    NAMES = {"pitwall_models_list", "pitwall_models_fit"}

    def test_models_tools_present(self) -> None:
        assert self.NAMES.issubset(TOOL_NAMES)


class TestToolRegistryCostGroup:
    NAMES = {
        "pitwall_cost_summary",
        "pitwall_recent_workloads",
        "pitwall_burn_rate",
    }

    def test_cost_tools_present(self) -> None:
        assert self.NAMES.issubset(TOOL_NAMES)


class TestToolRegistryGuardrailGroup:
    NAMES = {
        "pitwall_guardrail_status",
        "pitwall_guardrail_preview",
    }

    def test_guardrail_tools_present(self) -> None:
        assert self.NAMES.issubset(TOOL_NAMES)


class TestToolRegistryVolumeFileGroup:
    NAMES = {
        "pitwall_volume_list_objects",
        "pitwall_volume_read_chunk",
        "pitwall_volume_upload_object",
        "pitwall_volume_delete_object",
        "pitwall_pod_logs",
    }

    def test_volume_file_tools_present(self) -> None:
        assert self.NAMES.issubset(TOOL_NAMES)


class TestToolRegistryRunPodResourceGroup:
    def test_all_feature_specs_are_present(self) -> None:
        from pitwall.mcp.tools.runpod_resources import RUNPOD_RESOURCE_TOOL_SPECS

        expected = {spec.name for spec in RUNPOD_RESOURCE_TOOL_SPECS}
        assert len(expected) == 29
        assert expected.issubset(TOOL_NAMES)

    def test_market_catalogue_tool_is_present(self) -> None:
        assert "pitwall_runpod_catalogue" in TOOL_NAMES


class TestToolRegistryProviderOperationsGroup:
    NAMES = {
        "pitwall_provider_ops_list_descriptors",
        "pitwall_provider_ops_describe",
        "pitwall_provider_ops_availability",
        "pitwall_provider_ops_health",
    }

    def test_provider_operations_tools_present(self) -> None:
        assert self.NAMES.issubset(TOOL_NAMES)


class TestToolRegistryAdminGroup:
    NAMES = {
        "pitwall_create_capability",
        "pitwall_update_capability",
        "pitwall_create_provider",
        "pitwall_update_provider",
        "pitwall_disable_provider",
        "pitwall_hibernate_provider",
        "pitwall_audit_log",
    }

    def test_admin_tools_present(self) -> None:
        assert self.NAMES.issubset(TOOL_NAMES)


class TestToolRegistryCopilotGroup:
    NAMES = {
        "pitwall_copilot_propose",
    }

    def test_copilot_tools_present(self) -> None:
        assert self.NAMES.issubset(TOOL_NAMES)


class TestOutOfScopeAdminVerbsBlocked:
    """Prove enable_provider and kill-switch are NOT registered MCP tools.

    These are REST-only admin verbs. enable_provider is POST /v1/admin/providers/{id}/enable.
    kill-switch is POST /v1/admin/kill-switch. Neither is wired as an MCP tool.
    """

    def test_enable_provider_not_in_registry(self) -> None:
        assert "pitwall_enable_provider" not in TOOL_NAMES

    def test_kill_switch_not_in_registry(self) -> None:
        assert "pitwall_kill_switch" not in TOOL_NAMES
        assert "pitwall_kill-switch" not in TOOL_NAMES


class TestRegisterAll:
    def test_register_all_adds_tools_to_mcpserver(self) -> None:
        from mcp.server.mcpserver import MCPServer

        server = MCPServer("test_register")
        register_all(server)
        registered = {t.name for t in server._tool_manager.list_tools()}
        assert TOOL_NAMES.issubset(registered)

    def test_register_all_is_idempotent(self) -> None:
        from mcp.server.mcpserver import MCPServer

        server = MCPServer("test_idempotent")
        register_all(server)
        register_all(server)
        registered = {t.name for t in server._tool_manager.list_tools()}
        assert TOOL_NAMES.issubset(registered)


class TestToolSignaturesAreWireSafe:
    def test_no_handler_uses_var_keyword_params(self) -> None:
        """MCPServer builds each tool's JSON schema from the handler signature;
        ``**kwargs`` becomes a literal *required* field named ``kwargs``, which
        makes the tool uncallable over the MCP wire protocol (regression:
        pitwall_submit_inference advertised capability params via **kwargs and
        rejected every documented call with a pydantic validation error)."""
        import inspect

        for spec in TOOL_REGISTRY:
            offenders = [
                p.name
                for p in inspect.signature(spec.handler).parameters.values()
                if p.kind in (inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL)
            ]
            assert not offenders, f"{spec.name} uses variadic params {offenders}"
