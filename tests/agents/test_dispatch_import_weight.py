"""The agents dispatch path for a Model Studio route loads no service-only modules."""

from __future__ import annotations

import json
import subprocess
import sys

FORBIDDEN = (
    "fastapi",
    "uvicorn",
    "asyncpg",
    "redis",
    "arq",
    "textual",
    "runpod",
    "mcp",
    "prometheus_client",
)
MODULES = (
    "pitwall.agents.profiles_resolve",
    "pitwall.agents.profiles_probe",
    "pitwall.agents.endpoints",
    "pitwall.agents.harnesses.opencode",
    "pitwall.agents.harnesses.pi",
    "pitwall.agents.usage.accounts",
    "pitwall.agents.usage.model_studio",
    "pitwall.providers.model_studio.catalog",
    "pitwall.providers.model_studio.openapi",
)


def test_route_dispatch_path_imports_no_heavy_modules() -> None:
    code = (
        "import importlib, json, sys\n"
        f"for name in {MODULES!r}:\n"
        "    importlib.import_module(name)\n"
        f"print(json.dumps(sorted(m for m in {FORBIDDEN!r} if m in sys.modules)))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert json.loads(result.stdout) == []
