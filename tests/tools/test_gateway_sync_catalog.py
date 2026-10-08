import json
from pathlib import Path

import pytest

from tools.gateway import sync_catalog
from tools.gateway.catalog_schema import CatalogRow
from tools.gateway.sync_catalog import dedupe_pool_totals, load_rows, transform

FIXTURE = Path(__file__).parent / "fixtures" / "mini-catalog.json"
FORK_BASE_URL = "http://127.0.0.1:20130/v1"


def _rows() -> list[CatalogRow]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    rows, _covered = load_rows(
        payload["budgets"], payload["endpoints"], registry=payload.get("registry")
    )
    return rows


def test_pool_dedupe_counts_shared_pool_once_and_never_sums_uncapped_or_gated() -> None:
    totals = dedupe_pool_totals(_rows())
    assert (
        totals.steady_monthly == 5_000_000
    )  # alpha-pool counted once, gamma gated, delta uncapped
    assert totals.gated_tokens == 6_000_000
    assert totals.uncapped_providers == ("delta",)


def test_transform_emits_provider_rows_in_the_seed_shape_with_avoid_disabled() -> None:
    artifacts = transform(_rows(), curated_at="2026-09-03")
    providers = artifacts.catalog_json["providers"]
    by_name = {p["name"]: p for p in providers}
    assert by_name["gw-alpha-a1"]["cost"] == {"mode": "zero"}
    assert by_name["gw-alpha-a1"]["gateway"]["catalog"]["pool_key"] == "alpha-pool"
    assert by_name["gw-delta-d1"]["enabled"] is False  # tos avoid never enabled (ADR 0007)
    assert by_name["gw-gamma-g1"]["gateway"]["catalog"]["eligibility_gate"] == "regional-identity"
    assert by_name["gw-beta-b1"]["gateway"]["catalog"]["trains_on_prompts"] is True
    assert "providers:\n  - name: gw-alpha-a1" in artifacts.providers_yaml
    assert "  - name: coding.chat" in artifacts.capabilities_yaml


def test_transform_routes_non_keyless_rows_through_the_fork_url() -> None:
    artifacts = transform(_rows(), curated_at="2026-09-03")
    by_name = {p["name"]: p for p in artifacts.catalog_json["providers"]}
    # recurring-* rows (budgeted free pools) ride the loopback fork (Task 19)
    assert by_name["gw-alpha-a1"]["gateway"]["base_url"] == FORK_BASE_URL
    assert by_name["gw-alpha-a2"]["gateway"]["base_url"] == FORK_BASE_URL
    assert by_name["gw-gamma-g1"]["gateway"]["base_url"] == FORK_BASE_URL
    assert by_name["gw-delta-d1"]["gateway"]["base_url"] == FORK_BASE_URL
    assert by_name["gw-epsilon-e1"]["gateway"]["base_url"] == FORK_BASE_URL
    # keyless defaults through the fork too; --direct-keyless is what keeps it direct
    assert by_name["gw-beta-b1"]["gateway"]["base_url"] == FORK_BASE_URL


def test_direct_keyless_keeps_keyless_rows_direct_and_retargets_the_rest() -> None:
    artifacts = transform(_rows(), curated_at="2026-09-03", direct_keyless=True)
    by_name = {p["name"]: p for p in artifacts.catalog_json["providers"]}
    assert by_name["gw-beta-b1"]["gateway"]["base_url"] == "https://beta.example/v1"
    assert by_name["gw-alpha-a1"]["gateway"]["base_url"] == FORK_BASE_URL


def test_main_direct_keyless_flag_writes_keyless_rows_direct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "seed").mkdir()
    (tmp_path / "config").mkdir()
    monkeypatch.setattr(sync_catalog, "REPO_ROOT", tmp_path)
    rc = sync_catalog.main(["--version", "3.8.51", "--from-json", str(FIXTURE), "--direct-keyless"])
    assert rc == 0
    catalog = json.loads((tmp_path / "config" / "gateway-catalog.json").read_text(encoding="utf-8"))
    by_name = {p["name"]: p for p in catalog["providers"]}
    assert by_name["gw-beta-b1"]["gateway"]["base_url"] == "https://beta.example/v1"
    assert by_name["gw-alpha-a1"]["gateway"]["base_url"] == FORK_BASE_URL
    lock = json.loads(
        (tmp_path / "config" / "gateway-catalog.lock.json").read_text(encoding="utf-8")
    )
    assert lock["upstream_version"] == "3.8.51"
    assert (tmp_path / "seed" / "gateway-providers.yaml").read_text(encoding="utf-8").count(
        FORK_BASE_URL
    ) > 0


def test_main_default_routes_every_row_through_the_fork(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "seed").mkdir()
    (tmp_path / "config").mkdir()
    monkeypatch.setattr(sync_catalog, "REPO_ROOT", tmp_path)
    assert sync_catalog.main(["--version", "3.8.51", "--from-json", str(FIXTURE)]) == 0
    catalog = json.loads((tmp_path / "config" / "gateway-catalog.json").read_text(encoding="utf-8"))
    assert all(p["gateway"]["base_url"] == FORK_BASE_URL for p in catalog["providers"])


def test_transform_emits_ladder_fallback_chains() -> None:
    artifacts = transform(_rows(), curated_at="2026-09-03")
    by_name = {p["name"]: p for p in artifacts.catalog_json["providers"]}
    # keyless floor first, then budgeted free; avoid/gated rows never appear in any chain
    assert by_name["gw-beta-b1"]["fallback_chain"] == ["gw-alpha-a1", "gw-alpha-a2"]
    assert "gw-delta-d1" not in by_name["gw-alpha-a1"]["fallback_chain"]


def test_load_rows_strips_chat_responses_and_messages_path_segments() -> None:
    rows = {r.provider: r for r in _rows()}
    # alpha: from endpoints, original URL ended in /chat/completions
    assert rows["alpha"].base_url == "https://alpha.example/v1"
    # epsilon: from registry, original URL ended in /responses
    assert rows["epsilon"].base_url == "https://epsilon.example/v1"
    # zeta: from registry, no path suffix to strip
    assert rows["zeta"].base_url == "https://zeta.example/v1"
    # delta: from endpoints, /chat/completions stripped
    assert rows["delta"].base_url == "https://delta.example/v1"


def test_direct_ok_truth_table_for_format_executor_auth_combos() -> None:
    rows = {r.provider: r for r in _rows()}
    # Defaults (no registry entry): openai, default, apikey → direct_ok
    assert (rows["alpha"].upstream_format, rows["alpha"].executor, rows["alpha"].auth_type) == (
        "openai",
        "default",
        "apikey",
    )
    assert rows["alpha"].direct_ok is True
    # openai-responses format → not direct (Task 19 fork)
    assert rows["epsilon"].upstream_format == "openai-responses"
    assert rows["epsilon"].direct_ok is False
    # non-default executor (cheaperinference) → not direct
    assert rows["eta"].executor == "cheaperinference"
    assert rows["eta"].upstream_format == "openai"
    assert rows["eta"].direct_ok is False


def test_registry_fields_are_written_into_gateway_catalog() -> None:
    artifacts = transform(_rows(), curated_at="2026-09-03")
    by_name = {p["name"]: p for p in artifacts.catalog_json["providers"]}
    cat = by_name["gw-epsilon-e1"]["gateway"]["catalog"]
    assert cat["upstream_format"] == "openai-responses"
    assert cat["executor"] == "default"
    assert cat["auth_type"] == "apikey"
    assert cat["direct_ok"] is False
    # Defaults appear on a provider resolved through endpoints only
    a1 = by_name["gw-alpha-a1"]["gateway"]["catalog"]
    assert a1["upstream_format"] == "openai"
    assert a1["executor"] == "default"
    assert a1["auth_type"] == "apikey"
    assert a1["direct_ok"] is True


def test_known_unreachable_provider_is_skipped_silently() -> None:
    # agy is in KNOWN_UNREACHABLE; the fixture gives it a registry entry with
    # an empty baseUrl, no endpoint fallback — sync must drop it, not raise.
    rows = _rows()
    providers = {r.provider for r in rows}
    assert "agy" not in providers


def test_unknown_missing_provider_raises_value_error() -> None:
    # "phantom" is not in KNOWN_UNREACHABLE, not in registry, not in endpoints.
    payload = {
        "budgets": [
            {
                "provider": "phantom",
                "modelId": "phantom/p1",
                "displayName": "P",
                "monthlyTokens": 0,
                "creditTokens": 0,
                "freeType": "keyless",
                "poolKey": None,
                "tos": "ok",
                "hardStopGuaranteed": True,
            }
        ],
        "endpoints": {},
        "registry": {},
    }
    with pytest.raises(ValueError, match="phantom"):
        load_rows(payload["budgets"], payload["endpoints"], registry=payload["registry"])
