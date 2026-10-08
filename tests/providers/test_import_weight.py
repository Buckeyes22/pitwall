"""Import weight: light provider modules must not drag in the routing package."""

from __future__ import annotations

import subprocess
import sys

_PROBE = """
import sys
import {module}
loaded = sorted(name for name in sys.modules if name == "pitwall.routing" or name.startswith("pitwall.routing."))
print(",".join(loaded))
"""


def _routing_modules_loaded_by(module: str) -> list[str]:
    result = subprocess.run(
        [sys.executable, "-c", _PROBE.format(module=module)],
        capture_output=True,
        text=True,
        check=True,
    )
    return [name for name in result.stdout.strip().split(",") if name]


def test_gateway_adapter_does_not_import_routing() -> None:
    assert _routing_modules_loaded_by("pitwall.providers.gateway") == []


def test_provider_url_helpers_do_not_import_routing() -> None:
    assert _routing_modules_loaded_by("pitwall.resolver.provider_urls") == []
    assert _routing_modules_loaded_by("pitwall.resolver") == []


def test_resolver_service_still_loads_lazily() -> None:
    probe = (
        "import pitwall.resolver as r; "
        "assert callable(r.resolve_capability); "
        "assert r.Stage12Resolution.__name__ == 'Stage12Resolution'"
    )
    subprocess.run([sys.executable, "-c", probe], check=True)
