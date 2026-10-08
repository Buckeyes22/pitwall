"""Every ``[agents]`` settings field takes its value from the table and rejects a wrong type."""

from __future__ import annotations

from typing import Any

import pytest

from pitwall.config import ConfigFileError, agents_settings_from_toml

# (dotted field under [agents], a valid non-default value, a wrong-typed value)
FIELDS: tuple[tuple[str, Any, Any], ...] = (
    ("profiles", {"models": {"m": {"model": "x"}}}, 3),
    ("profiles.defaults", {"harness": "codex"}, 3),
    ("profiles.defaults.harness", "codex", 3),
    ("profiles.defaults.endpointHarness", "qwen", 3),
    ("profiles.endpoints", {"gpu": {"baseUrl": "http://gpu:8000/v1"}}, 3),
    ("profiles.harnesses", {"qwen": {"endpointAgnostic": True}}, 3),
    ("profiles.models", {"m": {"model": "x"}}, 3),
    ("profiles.pitwall", {"leases": {}}, 3),
)


def _nested(path: str, value: Any) -> dict[str, Any]:
    *parents, leaf = path.split(".")
    root: dict[str, Any] = {}
    node = root
    for part in parents:
        node = node.setdefault(part, {})
    node[leaf] = value
    return root


@pytest.mark.parametrize(("path", "value", "wrong"), FIELDS, ids=[field[0] for field in FIELDS])
def test_agents_field_takes_its_value_and_rejects_a_wrong_type(
    path: str, value: Any, wrong: Any
) -> None:
    parsed = agents_settings_from_toml(_nested(path, value))
    assert parsed.model_dump(exclude_unset=True) == _nested(path, value)

    with pytest.raises(ConfigFileError, match=r"\[agents\]"):
        agents_settings_from_toml(_nested(path, wrong))
