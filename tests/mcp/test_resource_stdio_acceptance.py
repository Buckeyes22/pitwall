"""Real resource-tool JSON-RPC transport with explicitly fake provider services."""

import sys

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

pytestmark = pytest.mark.anyio


async def test_resource_schema_results_and_errors_cross_real_stdio():
    script = """
from pitwall.mcp.tools import runpod_resources as tools
from pitwall.runpod_control_plane import (
 EndpointResource, HubTemplateResource, MutationResult, RunPodControlPlaneError,
 TemplateUpdateRequest, IdentifiedMutationRequest,
)
calls = 0
class Service:
 async def get_endpoint(self, resource_id):
  if resource_id == "missing":
   raise RunPodControlPlaneError("resource_not_found", "fixture missing", operation="get", resource_type="endpoint", resource_id=resource_id)
  return EndpointResource(id=resource_id, name=f"calls-{calls}", workers={"minimum":1,"maximum":7,"idle_timeout_seconds":91}, scaling={"type":"REQUEST_COUNT","value":9}, flashboot=True)
 async def search_hub_templates(self, query, *, limit):
  return [HubTemplateResource(id="hub_fixture",name=f"{query}:{limit}",image="fixture/image:1",serverless=True)]
 async def update_template(self, request):
  global calls
  calls += 1
  assert isinstance(request, TemplateUpdateRequest)
  return MutationResult(operation="template.update",resource_type="template",resource_id=request.resource_id,dry_run=request.dry_run,changed=False,effect=request.image,idempotency_key=request.idempotency_key,resource={"ports":request.ports,"disk_gb":request.disk_gb})
 async def delete_volume(self, request):
  global calls
  calls += 1
  assert isinstance(request, IdentifiedMutationRequest)
  return MutationResult(operation="volume.delete",resource_type="volume",resource_id=request.resource_id,dry_run=request.dry_run,changed=True,effect="deleted",idempotency_key=request.idempotency_key)
service = Service()
async def factory(*, mutation=False):
 return service
async def admit():
 pass
tools._service = factory
tools._admit_mutation = admit
from pitwall.mcp.__main__ import main
main()
"""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", script],
        env={
            "PITWALL_MCP_TRANSPORT": "stdio",
            "RUNPOD_API_KEY": "fixture-key",
            "DATABASE_URL": "postgresql://fixture:fixture@127.0.0.1/fixture",
            "REDIS_URL": "redis://127.0.0.1:1/0",
        },
    )
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool(
            "pitwall_runpod_get_endpoint", {"resource_id": "ep_selected"}
        )
        assert result.is_error is False
        assert result.structured_content["id"] == "ep_selected"
        assert result.structured_content["workers"] == {
            "minimum": 1,
            "maximum": 7,
            "idle_timeout_seconds": 91,
        }
        assert result.structured_content["scaling"] == {"type": "REQUEST_COUNT", "value": 9.0}
        result = await session.call_tool(
            "pitwall_runpod_search_hub_templates", {"query": "exact phrase", "limit": 3}
        )
        assert result.is_error is False
        assert result.structured_content["hub_templates"][0]["name"] == "exact phrase:3"
        payload = {
            "intent": "preview",
            "idempotency_key": "wire-template-001",
            "resource_id": "tpl_selected",
            "image": "fixture/image:2",
            "ports": ["8000/http"],
            "disk_gb": 30,
        }
        result = await session.call_tool("pitwall_runpod_update_template", {"request": payload})
        assert result.is_error is False
        assert result.structured_content["dry_run"] is True
        assert result.structured_content["resource"] == {"ports": ["8000/http"], "disk_gb": 30}
        assert result.structured_content["effect"] == "fixture/image:2"
        assert result.structured_content["idempotency_key"] == "wire-template-001"
        result = await session.call_tool(
            "pitwall_runpod_delete_volume",
            {
                "request": {
                    "intent": "apply",
                    "idempotency_key": "wire-delete-001",
                    "resource_id": "vol_selected",
                }
            },
        )
        assert result.is_error is False
        assert result.structured_content["changed"] is True
        assert result.structured_content["dry_run"] is False
        assert result.structured_content["resource_id"] == "vol_selected"
        result = await session.call_tool("pitwall_runpod_get_endpoint", {"resource_id": "missing"})
        assert result.is_error is True
        assert result.structured_content["error"] == "resource_not_found"
        canary = "private-invalid-size-canary"
        result = await session.call_tool(
            "pitwall_runpod_update_template", {"request": {**payload, "disk_gb": canary}}
        )
        assert result.is_error is True
        assert result.structured_content == {
            "error": "invalid_tool_arguments",
            "fields": ["request"],
        }
        assert canary not in str(result)
        result = await session.call_tool(
            "pitwall_runpod_get_endpoint", {"resource_id": "ep_counter"}
        )
        assert result.structured_content["name"] == "calls-2"
