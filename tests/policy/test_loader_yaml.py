"""A-46: the policy loader parses YAML with PyYAML, not a home-grown subset parser."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from pitwall.policy.loader import PolicyLoadError, _parse_policy_payload, load_policy_file

_DOCUMENT = """\
version: 1
policies:
  - id: provider.anchor-flow-block
    target: provider
    description: >-
      Folded block scalar that
      spans two lines.
    when: [&pod {path: provider_type, operator: equals, value: pod_lease}]
    rules:
      - &creds
        path: config.env_vars
        operator: contains_none
        value: [AWS_ACCESS_KEY_ID, R2_SECRET_KEY]
        message: |
          literal block
          keeps newlines
  - id: provider.alias-reuse
    target: provider
    when: [*pod]
    rules:
      - <<: *creds
        message: overridden
"""


def test_anchor_and_flow_and_block_scalars_parse(tmp_path: Path) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text(_DOCUMENT, encoding="utf-8")

    loaded = load_policy_file(path)

    policy = loaded.policies[0]
    assert policy.id == "provider.anchor-flow-block"
    assert policy.description == "Folded block scalar that spans two lines."
    assert policy.when[0].value == "pod_lease"
    rule = policy.rules[0]
    assert rule.operator.value == "contains_none"
    assert rule.value == ["AWS_ACCESS_KEY_ID", "R2_SECRET_KEY"]
    assert rule.message == "literal block\nkeeps newlines"
    reused = loaded.policies[1]
    assert reused.when[0].value == "pod_lease"
    assert reused.rules[0].value == ["AWS_ACCESS_KEY_ID", "R2_SECRET_KEY"]
    assert reused.rules[0].message == "overridden"


def test_matches_pyyaml() -> None:
    samples = [
        _DOCUMENT,
        "a: 1\nb: [1, 2, {c: d}]\ne: 'it''s'\nf: \"tab\\t\"\ng: 0o17\nh: 2026-01-01\n",
        "- one\n- two: [3]\n- |\n  text\n",
        "key: >\n  folded\n  text\n\nnext: ~\n",
    ]
    for sample in samples:
        assert _parse_policy_payload(sample) == yaml.safe_load(sample)


def test_invalid_yaml_is_a_policy_load_error(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("policies: [unterminated\n", encoding="utf-8")

    with pytest.raises(PolicyLoadError, match="broken.yaml"):
        load_policy_file(path)


def test_yaml_parse_error_never_quotes_the_policy_file(tmp_path: Path) -> None:
    path = tmp_path / "secret.yaml"
    path.write_text("version: 1\ntoken: hunter2\napi_key: sk-SECRET123: x\n", encoding="utf-8")

    with pytest.raises(PolicyLoadError) as caught:
        load_policy_file(path)
    message = str(caught.value)
    assert "line 3, column 22" in message
    assert "sk-SECRET123" not in message
    assert "hunter2" not in message
    assert caught.value.__cause__ is None
