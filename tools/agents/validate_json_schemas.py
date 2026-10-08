#!/usr/bin/env python3
"""Validate published schemas and representative runtime documents."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from pitwall.agents import profiles_toml  # noqa: E402  # reason: sys.path bootstrap
from pitwall.agents.doctor import (  # noqa: E402  # reason: sys.path bootstrap
    run_doctor,
)
from pitwall.agents.profiles import validate_profiles  # noqa: E402  # reason: sys.path bootstrap
from pitwall.agents.resources import (  # noqa: E402  # reason: sys.path bootstrap
    read_resource_json,
)
from pitwall.agents.setup import (  # noqa: E402  # reason: sys.path bootstrap
    load_install_specs,
)
from pitwall.providers.model_studio.catalog import (  # noqa: E402  # reason: sys.path bootstrap
    load_catalog as load_model_studio_catalog,
)
from tests.agents.shim_test_support import (  # noqa: E402  # reason: sys.path bootstrap
    ShimSandbox,
)

jsonschema = importlib.import_module("jsonschema")
ROUTE_FIXTURES = ROOT / "tests/agents" / "fixtures" / "routes"
SCHEMA_ID_PREFIX = (
    "https://raw.githubusercontent.com/Buckeyes22/pitwall/main/"
    "src/pitwall/agents/resources/schemas/"
)


def schema(name: str) -> dict[str, object]:
    document: dict[str, object] = read_resource_json(f"schemas/{name}")
    return document


def validate(name: str, instance: object) -> None:
    document = schema(name)
    jsonschema.Draft202012Validator.check_schema(document)
    jsonschema.Draft202012Validator(
        document,
        format_checker=jsonschema.FormatChecker(),
    ).validate(instance)


def validate_route_fixtures() -> None:
    document = schema("routes.schema.json")
    validator = jsonschema.Draft202012Validator(
        document,
        format_checker=jsonschema.FormatChecker(),
    )
    for path in sorted((ROUTE_FIXTURES / "positive").glob("*.json")):
        validator.validate(json.loads(path.read_text(encoding="utf-8")))
    for path in sorted((ROUTE_FIXTURES / "negative").glob("*.json")):
        instance = json.loads(path.read_text(encoding="utf-8"))
        if validator.is_valid(instance):
            raise AssertionError(
                f"negative route fixture unexpectedly passed schema validation: {path}"
            )


def main() -> int:
    schema_paths = sorted(
        (ROOT / "src" / "pitwall" / "agents" / "resources" / "schemas").glob("*.json")
    )
    if len(schema_paths) != 9:
        raise AssertionError(f"expected nine packaged schemas, found {len(schema_paths)}")
    for path in schema_paths:
        document = json.loads(path.read_text(encoding="utf-8"))
        expected_id = f"{SCHEMA_ID_PREFIX}{path.name}"
        if document.get("$id") != expected_id:
            raise AssertionError(f"{path}: expected $id {expected_id!r}")

    registry = read_resource_json("config/harness-registry.json")
    validate("harness-registry.schema.json", registry)
    validate("model-catalog.schema.json", read_resource_json("config/model-catalog.json"))
    validate("model-studio.schema.json", load_model_studio_catalog())
    example = ROOT / "examples/agents" / "profiles" / "pitwall.toml"
    # The TOML form omits schemaVersion; the product normalizes it back, so validate what it loads.
    table = profiles_toml.parse(example.read_text(encoding="utf-8"))
    validate("routes.schema.json", validate_profiles(table, registry=registry))
    validate_route_fixtures()
    installers = read_resource_json("config/harness-installers.json")
    validate("harness-installers.schema.json", installers)
    load_install_specs(system_name="Linux")
    load_install_specs(system_name="Darwin")
    for workflow in sorted((ROOT / "examples/agents").glob("*/workflow.json")):
        validate("workflow.schema.json", json.loads(workflow.read_text(encoding="utf-8")))

    report = run_doctor(
        ROOT,
        {"PATH": os.environ.get("PATH", "")},
        installation_only=True,
    )
    validate("doctor-result.schema.json", report)

    sandbox = ShimSandbox()
    try:
        sandbox.install_harness("codex")
        prompt = sandbox.prompt("schema validation\n")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "pitwall.agents",
                "dispatch",
                "codex",
                str(prompt),
            ],
            cwd=ROOT,
            env=sandbox.environment(),
            capture_output=True,
            check=False,
            timeout=30,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.decode("utf-8", errors="replace"))
        run = sandbox.run_directories()[0]
        validate(
            "dispatch-result.schema.json",
            json.loads((run / "result.json").read_text(encoding="utf-8")),
        )
        for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines():
            validate("lifecycle-event.schema.json", json.loads(line))
    finally:
        sandbox.cleanup()

    print("all schemas and representative runtime documents are valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
