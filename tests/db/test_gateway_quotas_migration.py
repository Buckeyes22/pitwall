from pathlib import Path

from pitwall.migrations import discover_migrations

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = _ROOT / "db/migrations/0033_gateway_quotas.sql"


def test_0033_widens_both_provider_checks() -> None:
    records = discover_migrations(_ROOT / "db/migrations")
    assert "0033_gateway_quotas" in {rec.version for rec in records}
    sql = _MIGRATION.read_text(encoding="utf-8")
    assert "providers_adapter_id_check" in sql
    assert "'openai_gateway'" in sql
    assert "CREATE TABLE pitwall.provider_quotas" in sql
    assert "CREATE TABLE pitwall.provider_quota_samples" in sql
    assert "CREATE TABLE pitwall.model_id_map" in sql
