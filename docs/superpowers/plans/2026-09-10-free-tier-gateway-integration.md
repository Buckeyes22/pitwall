# Free-Tier Gateway (Prong 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Land every integration item in `docs/research/2026-09-08-omniroute-free-tier-integration.md` (§7 architecture, §8 catalog sync, §9 Python engineering plan, §10 fork plan, §14 Phases 0–4) under the operator decisions recorded in that document's "Decisions (2026-09-10)" section.

**Architecture:** Pitwall gains a fifth provider adapter (`openai_gateway`) that speaks OpenAI-shape to a loopback Node gateway (`packages/gateway`, forked from OmniRoute) or directly to keyless upstreams; the planner grows quota, lockout, and zero-cost gates that are pure functions of an immutable `PlanningContext`; the catalog is synced as data (seeds + `config/gateway-catalog.json`) with a CI drift gate; a Claude-native `/v1/messages` surface translates to the same pipeline. Personal mode supervises the gateway as a child process behind `pitwall doctor`.

**Tech Stack:** Broker — Python 3.14, FastAPI, asyncpg, pydantic v2, httpx, pytest (root project, `uv run --frozen`). Gateway — Node 22 + TypeScript (forked `open-sse/` subset), own lockfile, never imported by the broker. Agent Routing — stdlib-only Python component.

**Spec:** `docs/research/2026-09-08-omniroute-free-tier-integration.md` (read §7–§11 and the Decisions section before any task). Reference checkout for extraction and fork: `~/git/OmniRoute` (upstream `diegosouzapw/OmniRoute` v3.8.51, MIT).

## Global Constraints

- Root commands run through `uv run --frozen`; component commands run from `packages/agent-routing` with `python3 -m unittest` and `.venv/bin/ruff`/`.venv/bin/mypy`; gateway commands run from `packages/gateway` with `npm ci`.
- Tests are hermetic: `tests/conftest.py` blocks provider DNS (`_block_real_runpod_control_plane_dns`); every adapter test uses `httpx.MockTransport`. No live calls to any free tier inside `pytest`.
- Fail-closed everywhere (ADR 0001/0005): unknown quota = ineligible for `zero` routing; missing pricing = ineligible; missing gateway config = refuse to boot.
- Credentials never appear in config, seeds, logs, or argv. Keyless providers must not require an env var to be set (`GatewayCredentials.api_key` is optional).
- `docs/sdlc/03-mcp-server.md`, `docs/support-matrix.md`, `README.md`, `tests/mcp/test_registry.py`, and the two `assert len(...) == 76` sites in `src/pitwall/mcp/registry.py` all state the MCP tool count; `tests/mcp/test_doc_count_sync.py` fails if they drift. Task 14 moves them to 78 together.
- Provider `provider_type` and `adapter_id` are CHECK-constrained in Postgres (`db/migrations/0002_providers.sql:45`, `0028_provider_neutral_runtime.sql:18`); new enum members need migration 0033 (Task 2) before any seed or API test that persists them.
- Every behavior change lands with its test in the same task. Commit after every task with `git commit -s`. Work on branch `feat/free-tier-gateway` off up-to-date `main`. Do not push unless asked.
- The broker never imports `packages/gateway`; the only coupling is loopback HTTP with a bearer token.
- Decisions (2026-09-10) bind this plan: Claude-native `/v1/messages` surface (Task 11); child-process supervision (Task 12); full 444-row catalog with avoid-list gating (Task 3); no `pitwall free` noun (Task 12); ADR 0007 first (Task 1).

## Lanes and file ownership

Tasks are ordered by dependency. Where two tasks share no files they may run as parallel lanes using `~/.claude/templates/lane-prompt.md`. Exclusive ownership:

| Lane | Tasks | Owns |
|---|---|---|
| A (broker core) | 1, 2, 4, 5, 6, 7, 8, 9 | `docs/decisions/0007-*`, `src/pitwall/core/enums.py`, `src/pitwall/core/models.py`, `db/migrations/0033_*`, `src/pitwall/seed.py`, `src/pitwall/providers/gateway.py`, `src/pitwall/providers/registry.py`, `src/pitwall/db/quota_repository.py`, `src/pitwall/routing/{quota,lockout,zero_cost,cascade_seed}.py`, `src/pitwall/routing/{planner,scoring,context,types,production}.py`, `src/pitwall/reconciler/__init__.py`, matching tests |
| B (catalog data) | 3, 17 | `tools/gateway/**`, `seed/gateway-*.yaml`, `config/gateway-catalog.json`, `docs/provenance/gateway-catalog-*.md`, `.github/workflows/ci.yml` (one new job only), `tests/tools/test_gateway_*.py` |
| C (surfaces) | 10, 11, 12, 13, 14, 15, 16 | `src/pitwall/api/routes/{quotas,gateway_models,messages}.py`, `src/pitwall/api/anthropic_translate.py`, `src/pitwall/api/app.py` (scope table + include_router), `src/pitwall/api/routes/openai.py` (model-id map only), `src/pitwall/cli.py`, `src/pitwall/cli_gateway.py`, `src/pitwall/personal/gateway.py`, `src/pitwall/tui/{providers,cost}.py`, `src/pitwall/mcp/**`, `src/pitwall/cost/exporter.py`, `packages/agent-routing/**`, `docs/api/openapi-baseline.json`, matching tests |
| D (gateway fork) | 18, 19 | `packages/gateway/**`, `.github/workflows/gateway-*.yml`, `NOTICE`, `docs/legal/gateway-*.md`, `docs/sdlc/24-gateway.md` |
| E (cross-prong) | 20 | `src/pitwall/cost/simulator.py`, `src/pitwall/autopilot/**`, `src/pitwall/routing/affinity.py`, matching tests |
| Serial | 21 | every `docs/sdlc/*.md` touched, `docs/support-matrix.md`, `docs/operator/current-providers.md`, `README.md` |

Lane C starts after Lane A Task 5 (it needs `QuotaRepository` and `QuotaSnapshot`). Lane D is independent of A–C until Task 19. Lane E starts after Task 9.

## File structure

New files, one responsibility each:

- `src/pitwall/providers/gateway.py` — the `openai_gateway` adapter: credentials, `infer`, `availability`, `pricing_model`, `QuotaExhausted`.
- `src/pitwall/db/quota_repository.py` — reads/writes for `provider_quotas`, `provider_quota_samples`, `model_id_map`.
- `src/pitwall/routing/quota.py` — `QuotaSnapshot`, `quota_eligible`, `quota_score_terms`.
- `src/pitwall/routing/lockout.py` — `(provider_id, model_id)` lockout state machine and 429 classifier.
- `src/pitwall/routing/zero_cost.py` — strict zero-cost evidence filter.
- `src/pitwall/routing/cascade_seed.py` — ladder ordering + emergency descent + escape-hatch message (pure functions used by the extractor and the planner).
- `src/pitwall/routing/affinity.py` — cache-affinity pinning term (Task 20).
- `src/pitwall/api/routes/quotas.py`, `src/pitwall/api/routes/gateway_models.py`, `src/pitwall/api/routes/messages.py`, `src/pitwall/api/anthropic_translate.py`.
- `src/pitwall/cli_gateway.py`, `src/pitwall/personal/gateway.py`.
- `src/pitwall/mcp/tools/gateway.py`.
- `tools/gateway/sync_catalog.py`, `tools/gateway/check_catalog_drift.py`, `tools/gateway/bench_free_pools.py`.
- `packages/gateway/` — the fork.

---

### Task 1: ADR 0007 — ToS posture and avoid-list contract

**Files:**
- Create: `docs/decisions/0007-free-tier-gateway-tos-posture.md`
- Modify: `docs/research/2026-09-08-omniroute-free-tier-integration.md` (status line, lines 4–8)
- Test: `tools/ci/check_markdown_links.py` (existing docs gate)

**Interfaces:**
- Produces: the vocabulary every later task keys off: `tos_verdict ∈ {ok, caution, ambiguous, avoid, unknown}` (copied verbatim from `open-sse/config/freeTierCatalog.ts:14`), routing rule "`avoid` and `unknown` are never routable; `ambiguous` and `caution` route only when `hard_stop_guaranteed` or `free_type == keyless`", and `eligibility_gate` rows never route.

- [x] **Step 1: Write the ADR**

```markdown
# ADR 0007: Free-tier gateway ToS posture and avoid-list contract

- Status: Accepted
- Date: 2026-09-10
- Decision owner: Project maintainer

## Context

Pitwall will route inference to free and cheap third-party pools catalogued by
OmniRoute (`open-sse/config/freeModelCatalog.data.ts`, 444 rows on v3.8.51).
Each row carries a hand-curated `tos` verdict (`ok | caution | ambiguous | avoid | unknown`,
`open-sse/config/freeTierCatalog.ts:14`), `freeType`, optional `hardStopGuaranteed`,
`trainsOnPrompts`, and `eligibilityGate`. Decision Q3 (2026-09-10) ships the full
catalog as seed data; Decision Q5 requires the posture that gates those rows to be
recorded before the seeds land.

## Decision

1. The verdict vocabulary is imported verbatim; Pitwall never re-grades a row.
2. Routing eligibility for `zero`-priced providers is:
   - `avoid` and `unknown`: never routable, never enabled by seeds.
   - `caution` and `ambiguous`: routable only when the row carries
     `hardStopGuaranteed: true` or `freeType == "keyless"`.
   - `ok`: routable subject to the same hard-stop/keyless evidence rule.
   - any row with `eligibilityGate`: never routable (counting-only upstream becomes
     routing-excluded here).
3. `trainsOnPrompts: true` is surfaced in every plan explanation, the TUI, and the
   CLI at selection time.
4. No ban-evasion, fingerprinting, or web-session executors are ported (principle 6 of
   the research document); permanent-ban signals are terminal operator-visible states.
5. The fork strip-list (ADR section "Fork strip-list") is completed by Task 18 of the
   implementation plan; until then it reads "pending".

## Fork strip-list

Pending until `packages/gateway` lands.

## Consequences

- Seeds may contain `avoid` rows with `enabled: false` so the catalog stays complete.
- `routing/zero_cost.py` is the single implementation of rule 2; the planner, CLI, and
  MCP read it, never re-derive it.
```

- [x] **Step 2: Point the research doc at the ADR**

Replace the status bullet lines 4–8 of the research doc with:

```markdown
- **Status:** Research artifact with an approved posture. ADR 0007
  (`docs/decisions/0007-free-tier-gateway-tos-posture.md`) records the ToS posture
  and avoid-list contract; the implementation plan is
  `docs/superpowers/plans/2026-09-10-free-tier-gateway-integration.md`.
```

- [x] **Step 3: Run the docs gate**

Run: `uv run --frozen python tools/ci/check_markdown_links.py`
Expected: exit 0, no broken internal links.

- [x] **Step 4: Commit**

```bash
git add docs/decisions/0007-free-tier-gateway-tos-posture.md docs/research/2026-09-08-omniroute-free-tier-integration.md
git commit -s -m "docs(adr): 0007 free-tier gateway ToS posture and avoid-list contract"
```

---

### Task 2: Enums, models, migration 0033, and seed support for `openai_gateway`

**Files:**
- Modify: `src/pitwall/core/enums.py:89-105` (`ProviderType`, `ProviderAdapterId`)
- Modify: `src/pitwall/core/models.py:338-346` (`default_credential_reference`)
- Create: `db/migrations/0033_gateway_quotas.sql`
- Modify: `src/pitwall/seed.py:340-400` (`_provider_from_seed`), `:425-478` (`_provider_config`)
- Modify: `src/pitwall/api/provider_schemas.py:122-140` (`validate_provider_registration_config`)
- Test: `tests/core/test_gateway_enums.py`, `tests/db/test_gateway_quotas_migration.py`, `tests/test_gateway_seed.py` (beside `tests/test_seed.py`, reusing its `_fake_repos` pattern at `tests/test_seed.py:187-205`)

**Interfaces:**
- Produces: `ProviderType.OPENAI_GATEWAY = "openai_gateway"`, `ProviderAdapterId.GATEWAY = "openai_gateway"`, `default_credential_reference(ProviderAdapterId.GATEWAY) == "PITWALL_GATEWAY_API_KEY"`, tables `pitwall.provider_quotas`, `pitwall.provider_quota_samples`, `pitwall.model_id_map`, and a seed row shape where `gpu_class` is optional and `adapter: openai_gateway` sets `adapter_id`.

- [x] **Step 1: Write the failing tests**

`tests/core/test_gateway_enums.py`:

```python
from pitwall.core.enums import ProviderAdapterId, ProviderType
from pitwall.core.models import default_credential_reference


def test_gateway_enum_members_exist() -> None:
    assert ProviderType.OPENAI_GATEWAY == "openai_gateway"
    assert ProviderAdapterId.GATEWAY == "openai_gateway"


def test_gateway_default_credential_reference_is_optional_key_env() -> None:
    assert default_credential_reference(ProviderAdapterId.GATEWAY) == "PITWALL_GATEWAY_API_KEY"
```

`tests/db/test_gateway_quotas_migration.py` (mirror `tests/db/test_normalize_route_plan_cost_quotes_migration.py`):

```python
from pathlib import Path

from pitwall.migrations import discover_migrations

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = _ROOT / "db/migrations/0033_gateway_quotas.sql"


def test_0033_is_the_latest_migration_and_widens_both_checks() -> None:
    records = discover_migrations(_ROOT / "db/migrations")
    assert records[-1].version == "0033_gateway_quotas"
    sql = _MIGRATION.read_text(encoding="utf-8")
    assert "providers_adapter_id_check" in sql
    assert "'openai_gateway'" in sql
    assert "CREATE TABLE pitwall.provider_quotas" in sql
    assert "CREATE TABLE pitwall.provider_quota_samples" in sql
    assert "CREATE TABLE pitwall.model_id_map" in sql
```

`tests/test_gateway_seed.py`:

```python
from unittest.mock import AsyncMock, MagicMock

import pytest

from pitwall import seed
from pitwall.core.enums import CapabilitySource, ProviderAdapterId, ProviderType
from pitwall.seed import SeedValidationError, apply_seed_data


def _fake_repos(monkeypatch: pytest.MonkeyPatch) -> None:
    cap_repo = MagicMock()
    cap_repo.get_by_name = AsyncMock(return_value=None)
    cap_repo.get = AsyncMock(return_value=None)
    cap_repo.create = AsyncMock(side_effect=lambda cap: cap)
    prov_repo = MagicMock()
    prov_repo.get_by_name = AsyncMock(return_value=None)
    prov_repo.create = AsyncMock(side_effect=lambda prov: prov)
    monkeypatch.setattr(seed, "CapabilityRepository", lambda _pool: cap_repo)
    monkeypatch.setattr(seed, "ProviderRepository", lambda _pool: prov_repo)


def _payload(provider_type: str = "openai_gateway", adapter: str | None = "openai_gateway") -> dict:
    provider = {
        "name": "gw-keyless-demo",
        "capability": "coding.chat",
        "endpoint_id": "keyless-demo",
        "provider_type": provider_type,
        "priority": 50,
        "cost": {"mode": "zero"},
        "gateway": {
            "base_url": "http://127.0.0.1:20130/v1",
            "model_id": "demo/free",
            "catalog": {"free_type": "keyless", "tos": "ok", "hard_stop_guaranteed": True},
        },
    }
    if adapter is not None:
        provider["adapter"] = adapter
    return {
        "capabilities": [{"name": "coding.chat", "class": "llm", "cost_mode": "zero"}],
        "providers": [provider],
    }


@pytest.mark.anyio
async def test_gateway_seed_row_needs_no_gpu_class_and_sets_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_repos(monkeypatch)
    result = await apply_seed_data(_payload(), pool=MagicMock(), source=CapabilitySource.YAML)
    [provider] = result.providers
    assert provider.provider_type == ProviderType.OPENAI_GATEWAY
    assert provider.adapter_id == ProviderAdapterId.GATEWAY
    assert provider.config["gateway"]["model_id"] == "demo/free"
    assert provider.config["openai_base_url"] == "http://127.0.0.1:20130/v1"
    assert "gpu_class" not in provider.config


@pytest.mark.anyio
async def test_gateway_row_without_matching_adapter_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_repos(monkeypatch)
    with pytest.raises(SeedValidationError, match="adapter: openai_gateway"):
        await apply_seed_data(
            _payload(adapter=None), pool=MagicMock(), source=CapabilitySource.YAML
        )


@pytest.mark.anyio
async def test_non_gateway_seed_row_still_requires_gpu_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_repos(monkeypatch)
    with pytest.raises(SeedValidationError, match="gpu_class"):
        await apply_seed_data(
            _payload("public_endpoint", adapter=None),
            pool=MagicMock(),
            source=CapabilitySource.YAML,
        )
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen pytest tests/core/test_gateway_enums.py tests/db/test_gateway_quotas_migration.py tests/test_gateway_seed.py -q`
Expected: FAIL — `AttributeError: OPENAI_GATEWAY`, migration `0032…` is latest, `SeedValidationError: provider.gpu_class`.

- [x] **Step 3: Implement enums and credential default**

`src/pitwall/core/enums.py`:

```python
class ProviderType(StrEnum):
    """Provider surfaces supported by Pitwall."""

    SERVERLESS_QUEUE = "serverless_queue"
    SERVERLESS_LB = "serverless_lb"
    PUBLIC_ENDPOINT = "public_endpoint"
    POD_LEASE = "pod_lease"
    OPENAI_GATEWAY = "openai_gateway"


class ProviderAdapterId(StrEnum):
    RUNPOD = "runpod"
    VAST = "vast"
    TOGETHER = "together"
    LAMBDA_CLOUD = "lambda_cloud"
    GATEWAY = "openai_gateway"
```

`src/pitwall/core/models.py` `default_credential_reference` map gains `ProviderAdapterId.GATEWAY: "PITWALL_GATEWAY_API_KEY"`.

- [x] **Step 4: Write migration 0033**

`db/migrations/0033_gateway_quotas.sql`:

```sql
-- Free-tier gateway (ADR 0007): new provider type/adapter, quota state, and
-- the proxy model-id map. Idempotency, workloads, and cost rollups are unchanged.

ALTER TABLE pitwall.providers DROP CONSTRAINT IF EXISTS providers_provider_type_check;
ALTER TABLE pitwall.providers ADD CONSTRAINT providers_provider_type_check CHECK (
  provider_type IN ('serverless_queue', 'serverless_lb', 'public_endpoint', 'pod_lease', 'openai_gateway')
);
ALTER TABLE pitwall.providers DROP CONSTRAINT IF EXISTS providers_adapter_id_check;
ALTER TABLE pitwall.providers ADD CONSTRAINT providers_adapter_id_check CHECK (
  adapter_id IN ('runpod', 'vast', 'together', 'lambda_cloud', 'openai_gateway')
);

CREATE TABLE pitwall.provider_quotas (
  provider_id  TEXT NOT NULL REFERENCES pitwall.providers(id) ON DELETE CASCADE,
  pool_key     TEXT NOT NULL DEFAULT '',
  free_type    TEXT NOT NULL CHECK (free_type IN
                 ('recurring-daily','recurring-monthly','recurring-credit','recurring-uncapped',
                  'one-time-initial','keyless','discontinued')),
  window_start TIMESTAMPTZ,
  reset_at     TIMESTAMPTZ,
  budget_units TEXT,
  used_units   TEXT NOT NULL DEFAULT '0',
  tos_verdict  TEXT NOT NULL CHECK (tos_verdict IN ('ok','caution','ambiguous','avoid','unknown')),
  evidence     JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (provider_id, pool_key)
);

CREATE TABLE pitwall.provider_quota_samples (
  provider_id TEXT NOT NULL REFERENCES pitwall.providers(id) ON DELETE CASCADE,
  sampled_at  TIMESTAMPTZ NOT NULL,
  used_units  TEXT NOT NULL,
  reset_at    TIMESTAMPTZ,
  PRIMARY KEY (provider_id, sampled_at)
);
CREATE INDEX idx_provider_quota_samples_recent ON pitwall.provider_quota_samples (sampled_at DESC);

CREATE TABLE pitwall.model_id_map (
  model_id    TEXT PRIMARY KEY,
  capability  TEXT NOT NULL,
  provider    TEXT NOT NULL REFERENCES pitwall.providers(id) ON DELETE CASCADE
);
```

The constraint name `providers_provider_type_check` is the Postgres default for the inline CHECK in `0002_providers.sql:45`; the `DROP … IF EXISTS` keeps the migration re-runnable on the testinfra database.

- [x] **Step 5: Implement seed support**

In `src/pitwall/seed.py` `_provider_from_seed`, replace the unconditional `gpu_class` requirement:

```python
    adapter_id = _string_choice(
        _first_present(spec, ("adapter", "adapter_id"), default=ProviderAdapterId.RUNPOD.value),
        ProviderAdapterId,
        "provider.adapter",
    )
    if provider_type == ProviderType.OPENAI_GATEWAY:
        gpu_class = None
        if adapter_id != ProviderAdapterId.GATEWAY:
            raise SeedValidationError("openai_gateway providers require adapter: openai_gateway")
    else:
        gpu_class = validate_canonical_gpu_name(
            _required_string(spec, "gpu_class", "provider.gpu_class")
        )
    config = _provider_config(spec, provider_type=provider_type, endpoint_id=endpoint_id)
    if gpu_class is not None:
        config["gpu_class"] = gpu_class
```

and pass `adapter_id=adapter_id` to the `Provider(...)` constructor. In `_provider_config`, after the `request_timeout_s` block:

```python
    if provider_type == ProviderType.OPENAI_GATEWAY:
        gateway = dict(_dict_value(spec.get("gateway", config.get("gateway", {})), "provider.gateway"))
        if not _optional_string(gateway.get("base_url")):
            raise SeedValidationError("openai_gateway providers require gateway.base_url")
        if not _optional_string(gateway.get("model_id")):
            raise SeedValidationError("openai_gateway providers require gateway.model_id")
        gateway["catalog"] = dict(_dict_value(gateway.get("catalog", {}), "provider.gateway.catalog"))
        config["gateway"] = gateway
        config["openai_base_url"] = gateway["base_url"]
```

In `src/pitwall/api/provider_schemas.py` `_validate_url_config`, treat `ProviderType.OPENAI_GATEWAY` like `PUBLIC_ENDPOINT` for `openai_base_url` validation (the SSRF regex path already applied to public endpoints), and in `_validate_registered_endpoint` accept the `endpoint_id` allow-list regex for gateway rows.

- [x] **Step 6: Run tests to verify they pass**

Run: `uv run --frozen pytest tests/core/test_gateway_enums.py tests/db/test_gateway_quotas_migration.py tests/test_gateway_seed.py tests/test_seed.py tests/api/test_capabilities_contract.py tests/providers -q`
Expected: all PASS.

- [x] **Step 7: Run the integration migration lane**

Run: `make up && make test-db 2>&1 | tail -3`
Expected: `passed` line with 0 failures (migration 0033 applies on testinfra Postgres).

- [x] **Step 8: Commit**

```bash
git add src/pitwall/core/enums.py src/pitwall/core/models.py db/migrations/0033_gateway_quotas.sql src/pitwall/seed.py src/pitwall/api/provider_schemas.py tests/core/test_gateway_enums.py tests/db/test_gateway_quotas_migration.py tests/test_gateway_seed.py
git commit -s -m "feat(core): openai_gateway provider type, adapter id, quota tables, and gateway seed rows"
```

---

### Task 3: Catalog extraction, seeds, `config/gateway-catalog.json`, provenance, and drift gate

> **Grounding correction (2026-09-10, found during execution):** `src/shared/constants/config.ts`
> `PROVIDER_ENDPOINTS` covers only 12 of the 77 catalog providers. The real per-provider source is
> `open-sse/config/providers/registry/<id>/index.ts` (`baseUrl`, `format`, `executor`, `authType`),
> which covers 73 of 77; `agy`, `arcee-ai`, `glm-cn`, and `opencode-zen` have no HTTP base and are
> the only permitted skips (`KNOWN_UNREACHABLE`). Debian's Node 22 lacks `--experimental-strip-types`
> support for these files, so extraction is a regex walker (`tools/gateway/extract_upstream.mjs`).
> Rows record `upstream_format`/`executor`/`auth_type`; only `format == "openai"` rows are directly
> reachable by the Python adapter, the rest go through the fork (Task 19).

**Files:**
- Create: `tools/gateway/__init__.py`, `tools/gateway/sync_catalog.py`, `tools/gateway/check_catalog_drift.py`, `tools/gateway/catalog_schema.py`
- Create: `seed/gateway-capabilities.yaml`, `seed/gateway-providers.yaml`, `config/gateway-catalog.json`, `config/gateway-catalog.lock.json`
- Create: `docs/provenance/gateway-catalog-2026-09-10.md`, `docs/legal/gateway-attribution-review.md`
- Modify: `.github/workflows/ci.yml` (new job `gateway-catalog-drift` after `docs`)
- Test: `tests/tools/test_gateway_sync_catalog.py`, `tests/tools/test_gateway_catalog_drift.py`, `tests/tools/fixtures/mini-catalog.json`

**Interfaces:**
- Consumes: enum members and seed row shape from Task 2.
- Produces: `catalog_schema.CatalogRow` (frozen dataclass: `provider, model_id, display_name, monthly_tokens: int, credit_tokens: int, free_type, pool_key: str | None, tos, trains_on_prompts: bool, hard_stop_guaranteed: bool, eligibility_gate: str | None, base_url: str, enabled: bool`), `sync_catalog.transform(rows, endpoints) -> CatalogArtifacts(capabilities_yaml: str, providers_yaml: str, catalog_json: dict)`, `sync_catalog.dedupe_pool_totals(rows) -> PoolTotals(steady_monthly: int, recurring_credit: int, one_time: int, uncapped_providers: tuple[str, ...], gated_tokens: int)`, and `check_catalog_drift.compare(current: dict, locked: dict) -> DriftReport(hard_failures: list[str], warnings: list[str])`.

- [x] **Step 1: Write the fixture and failing tests**

`tests/tools/fixtures/mini-catalog.json` (synthetic, four rows exercising every dedupe rule):

```json
{
  "curatedAt": "2026-09-03",
  "budgets": [
    {"provider": "alpha", "modelId": "alpha/a1", "displayName": "A1", "monthlyTokens": 5000000, "creditTokens": 0, "freeType": "recurring-monthly", "poolKey": "alpha-pool", "tos": "ok", "hardStopGuaranteed": true},
    {"provider": "alpha", "modelId": "alpha/a2", "displayName": "A2", "monthlyTokens": 5000000, "creditTokens": 0, "freeType": "recurring-monthly", "poolKey": "alpha-pool", "tos": "ok", "hardStopGuaranteed": true},
    {"provider": "beta",  "modelId": "beta/b1",  "displayName": "B1", "monthlyTokens": 0, "creditTokens": 0, "freeType": "keyless", "poolKey": null, "tos": "caution", "trainsOnPrompts": true},
    {"provider": "gamma", "modelId": "gamma/g1", "displayName": "G1", "monthlyTokens": 6000000, "creditTokens": 0, "freeType": "recurring-monthly", "poolKey": "gamma", "tos": "ok", "eligibilityGate": "regional-identity"},
    {"provider": "delta", "modelId": "delta/d1", "displayName": "D1", "monthlyTokens": 0, "creditTokens": 0, "freeType": "recurring-uncapped", "poolKey": null, "tos": "avoid"}
  ],
  "endpoints": {"alpha": "https://alpha.example/v1/chat/completions", "beta": "https://beta.example/v1/chat/completions", "gamma": "https://gamma.example/v1", "delta": "https://delta.example/v1/chat/completions"}
}
```

`tests/tools/test_gateway_sync_catalog.py`:

```python
import json
from pathlib import Path

from tools.gateway.catalog_schema import CatalogRow
from tools.gateway.sync_catalog import dedupe_pool_totals, load_rows, transform

FIXTURE = Path(__file__).parent / "fixtures" / "mini-catalog.json"


def _rows() -> list[CatalogRow]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return load_rows(payload["budgets"], payload["endpoints"])


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
    assert by_name["gw-alpha-a1"]["gateway"]["base_url"] == "https://alpha.example/v1"
    assert by_name["gw-delta-d1"]["enabled"] is False  # tos avoid never enabled (ADR 0007)
    assert by_name["gw-gamma-g1"]["gateway"]["catalog"]["eligibility_gate"] == "regional-identity"
    assert by_name["gw-beta-b1"]["gateway"]["catalog"]["trains_on_prompts"] is True
    assert "providers:\n  - name: gw-alpha-a1" in artifacts.providers_yaml
    assert "  - name: coding.chat" in artifacts.capabilities_yaml


def test_transform_emits_ladder_fallback_chains() -> None:
    artifacts = transform(_rows(), curated_at="2026-09-03")
    by_name = {p["name"]: p for p in artifacts.catalog_json["providers"]}
    # keyless floor first, then budgeted free; avoid/gated rows never appear in any chain
    assert by_name["gw-beta-b1"]["fallback_chain"] == ["gw-alpha-a1", "gw-alpha-a2"]
    assert "gw-delta-d1" not in by_name["gw-alpha-a1"]["fallback_chain"]
```

`tests/tools/test_gateway_catalog_drift.py`:

```python
from tools.gateway.check_catalog_drift import compare


def _snapshot(**overrides):
    base = {"steady_monthly": 5_000_000, "pool_count": 1, "avoid_list": ["delta"], "row_count": 5}
    base.update(overrides)
    return base


def test_avoid_list_change_is_a_hard_failure() -> None:
    report = compare(_snapshot(avoid_list=["delta", "beta"]), _snapshot())
    assert report.hard_failures == ["avoid-list changed: +['beta'] -[]"]


def test_count_change_is_a_warning_only() -> None:
    report = compare(_snapshot(row_count=6, steady_monthly=5_500_000), _snapshot())
    assert report.hard_failures == []
    assert report.warnings == ["row_count 5 -> 6", "steady_monthly 5000000 -> 5500000"]
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen pytest tests/tools/test_gateway_sync_catalog.py tests/tools/test_gateway_catalog_drift.py -q`
Expected: FAIL — `ModuleNotFoundError: tools.gateway`.

- [x] **Step 3: Implement `catalog_schema.py`**

```python
"""Typed rows for the synced free-tier catalog (ADR 0007 vocabulary)."""

from __future__ import annotations

from dataclasses import dataclass

FREE_TYPES = frozenset(
    {
        "recurring-daily",
        "recurring-monthly",
        "recurring-credit",
        "recurring-uncapped",
        "one-time-initial",
        "keyless",
        "discontinued",
    }
)
TOS_VERDICTS = frozenset({"ok", "caution", "ambiguous", "avoid", "unknown"})
STEADY_MONTHLY = frozenset({"recurring-daily", "recurring-monthly"})
RECURRING_CREDIT = frozenset({"recurring-credit"})
ONE_TIME = frozenset({"one-time-initial"})
UNCAPPED = frozenset({"recurring-uncapped"})


@dataclass(frozen=True, slots=True)
class CatalogRow:
    provider: str
    model_id: str
    display_name: str
    monthly_tokens: int
    credit_tokens: int
    free_type: str
    pool_key: str | None
    tos: str
    base_url: str
    trains_on_prompts: bool = False
    hard_stop_guaranteed: bool = False
    eligibility_gate: str | None = None

    def __post_init__(self) -> None:
        if self.free_type not in FREE_TYPES:
            raise ValueError(
                f"unknown freeType {self.free_type!r} for {self.provider}/{self.model_id}"
            )
        if self.tos not in TOS_VERDICTS:
            raise ValueError(f"unknown tos {self.tos!r} for {self.provider}/{self.model_id}")
        if not self.base_url.startswith("https://") and not self.base_url.startswith(
            "http://127.0.0.1"
        ):
            raise ValueError(f"base_url must be https or loopback: {self.base_url}")

    @property
    def routable(self) -> bool:
        """ADR 0007 rule 2, evaluated on catalog evidence alone."""
        if self.tos in {"avoid", "unknown"} or self.eligibility_gate is not None:
            return False
        if self.free_type == "discontinued":
            return False
        return self.hard_stop_guaranteed or self.free_type == "keyless"

    @property
    def seed_name(self) -> str:
        slug = self.model_id.split("/")[-1].lower().replace("_", "-").replace(".", "-")
        return f"gw-{self.provider}-{slug}"
```

- [x] **Step 4: Implement `sync_catalog.py`**

```python
"""Extract OmniRoute's free-tier catalog into Pitwall seeds and config (§8)."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools.gateway.catalog_schema import (
    ONE_TIME,
    RECURRING_CREDIT,
    STEADY_MONTHLY,
    UNCAPPED,
    CatalogRow,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
NODE_EXTRACT = (
    "import('./open-sse/config/freeModelCatalog.ts').then(async m => {"
    " const c = await import('./src/shared/constants/config.ts');"
    " process.stdout.write(JSON.stringify({curatedAt: m.FREE_CATALOG_CURATED_AT,"
    " budgets: m.FREE_MODEL_BUDGETS, endpoints: c.PROVIDER_ENDPOINTS})) })"
)


@dataclass(frozen=True, slots=True)
class PoolTotals:
    steady_monthly: int
    recurring_credit: int
    one_time: int
    uncapped_providers: tuple[str, ...]
    gated_tokens: int


@dataclass(frozen=True, slots=True)
class CatalogArtifacts:
    capabilities_yaml: str
    providers_yaml: str
    catalog_json: dict[str, Any]


def load_rows(
    budgets: Sequence[Mapping[str, Any]], endpoints: Mapping[str, str]
) -> list[CatalogRow]:
    rows: list[CatalogRow] = []
    for raw in budgets:
        if raw.get("enabled") is False:
            continue
        endpoint = endpoints.get(raw["provider"])
        if not endpoint:
            raise ValueError(f"no endpoint for provider {raw['provider']!r}")
        # src/shared/constants/config.ts PROVIDER_ENDPOINTS values end in the
        # chat path (e.g. "https://openrouter.ai/api/v1/chat/completions");
        # the adapter appends "chat/completions" itself, so keep the base only.
        base_url = endpoint.removesuffix("/chat/completions").rstrip("/")
        rows.append(
            CatalogRow(
                provider=raw["provider"],
                model_id=raw["modelId"],
                display_name=raw.get("displayName", raw["modelId"]),
                monthly_tokens=int(raw.get("monthlyTokens", 0)),
                credit_tokens=int(raw.get("creditTokens", 0)),
                free_type=raw["freeType"],
                pool_key=raw.get("poolKey"),
                tos=raw["tos"],
                base_url=base_url,
                trains_on_prompts=bool(raw.get("trainsOnPrompts", False)),
                hard_stop_guaranteed=bool(raw.get("hardStopGuaranteed", False)),
                eligibility_gate=raw.get("eligibilityGate"),
            )
        )
    return sorted(rows, key=lambda r: (r.provider, r.model_id))


def dedupe_pool_totals(rows: Sequence[CatalogRow]) -> PoolTotals:
    """§8.1 rule 3: shared poolKey counts once; uncapped never summed; gated never admitted."""
    seen_pools: set[str] = set()
    steady = credit = one_time = gated = 0
    uncapped: list[str] = []
    for row in rows:
        gated_row = row.eligibility_gate is not None
        if row.free_type in UNCAPPED:
            if not gated_row and row.provider not in uncapped:
                uncapped.append(row.provider)
            continue
        key = row.pool_key or f"{row.provider}/{row.model_id}"
        if key in seen_pools:
            continue
        seen_pools.add(key)
        if row.free_type in STEADY_MONTHLY:
            if gated_row:
                gated += row.monthly_tokens
            else:
                steady += row.monthly_tokens
        elif row.free_type in RECURRING_CREDIT and not gated_row:
            credit += row.credit_tokens
        elif row.free_type in ONE_TIME and not gated_row:
            one_time += row.credit_tokens
    return PoolTotals(steady, credit, one_time, tuple(uncapped), gated)


def _ladder_rank(row: CatalogRow) -> int:
    # own-serve (0) is not a catalog row; keyless (1) → budgeted free (2) → cheap metered (3)
    if row.free_type == "keyless":
        return 1
    return 2


def _provider_entry(row: CatalogRow, chain: list[str]) -> dict[str, Any]:
    return {
        "name": row.seed_name,
        "capability": "coding.chat",
        "endpoint_id": row.seed_name.removeprefix("gw-")[:64],
        "provider_type": "openai_gateway",
        "adapter": "openai_gateway",
        "region": "GLOBAL",
        "priority": 50 if row.free_type == "keyless" else 60,
        "enabled": row.routable,
        "cost": {"mode": "zero"},
        "fallback_chain": chain,
        "gateway": {
            "base_url": row.base_url,
            "model_id": row.model_id,
            "catalog": {
                "free_type": row.free_type,
                "tos": row.tos,
                "trains_on_prompts": row.trains_on_prompts,
                "hard_stop_guaranteed": row.hard_stop_guaranteed,
                "pool_key": row.pool_key,
                "monthly_tokens": row.monthly_tokens,
                "credit_tokens": row.credit_tokens,
                "eligibility_gate": row.eligibility_gate,
                "display_name": row.display_name,
            },
        },
    }


def transform(rows: Sequence[CatalogRow], *, curated_at: str) -> CatalogArtifacts:
    routable = [r for r in rows if r.routable]
    ladder = sorted(routable, key=lambda r: (_ladder_rank(r), r.provider, r.model_id))
    names = [r.seed_name for r in ladder]
    providers: list[dict[str, Any]] = []
    for row in rows:
        chain = [n for n in names if n != row.seed_name] if row.routable else []
        providers.append(_provider_entry(row, chain))
    totals = dedupe_pool_totals(rows)
    catalog_json = {
        "schema_version": 1,
        "curated_at": curated_at,
        "totals": totals.__dict__ | {"uncapped_providers": list(totals.uncapped_providers)},
        "avoid_list": sorted({r.provider for r in rows if r.tos == "avoid"}),
        "providers": providers,
    }
    capabilities_yaml = (
        "capabilities:\n"
        "  - name: coding.chat\n"
        "    version: 1.0.0\n"
        "    class: llm\n"
        "    description: Free-tier gateway chat capability (ADR 0007).\n"
        "    cost_mode: zero\n"
        "    input_schema:\n      type: object\n"
        "    output_schema:\n      type: object\n"
    )
    providers_yaml = "providers:\n" + "".join(_yaml_provider(p) for p in providers)
    return CatalogArtifacts(capabilities_yaml, providers_yaml, catalog_json)


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return '"' + str(value).replace('"', '\\"') + '"'


def _yaml_provider(p: Mapping[str, Any]) -> str:
    lines = [f"  - name: {p['name']}"]
    for key in (
        "capability",
        "endpoint_id",
        "provider_type",
        "adapter",
        "region",
        "priority",
        "enabled",
    ):
        lines.append(f"    {key}: {_yaml_scalar(p[key])}")
    lines.append("    cost:\n      mode: zero")
    lines.append("    fallback_chain:" + ("" if p["fallback_chain"] else " []"))
    lines.extend(f"      - {n}" for n in p["fallback_chain"])
    gw = p["gateway"]
    lines.append("    gateway:")
    lines.append(f"      base_url: {_yaml_scalar(gw['base_url'])}")
    lines.append(f"      model_id: {_yaml_scalar(gw['model_id'])}")
    lines.append("      catalog:")
    lines.extend(f"        {k}: {_yaml_scalar(v)}" for k, v in gw["catalog"].items())
    return "\n".join(lines) + "\n"


def extract_from_npm(version: str, prefix: Path) -> dict[str, Any]:
    """Install the pinned upstream release into *prefix* and emit the catalog JSON once."""
    subprocess.run(
        ["npm", "install", "--prefix", str(prefix), "--ignore-scripts", f"omniroute@{version}"],
        check=True,
    )
    pkg = prefix / "node_modules" / "omniroute"
    out = subprocess.run(
        ["node", "--experimental-strip-types", "-e", NODE_EXTRACT],
        cwd=pkg,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(out.stdout)


def write_artifacts(artifacts: CatalogArtifacts, *, version: str, tarball_sha256: str) -> None:
    (REPO_ROOT / "seed" / "gateway-capabilities.yaml").write_text(
        artifacts.capabilities_yaml, encoding="utf-8"
    )
    (REPO_ROOT / "seed" / "gateway-providers.yaml").write_text(
        artifacts.providers_yaml, encoding="utf-8"
    )
    (REPO_ROOT / "config" / "gateway-catalog.json").write_text(
        json.dumps(artifacts.catalog_json, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lock = {
        "upstream_version": version,
        "tarball_sha256": tarball_sha256,
        "extractor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "steady_monthly": artifacts.catalog_json["totals"]["steady_monthly"],
        "pool_count": len(
            {
                p["gateway"]["catalog"]["pool_key"]
                for p in artifacts.catalog_json["providers"]
                if p["gateway"]["catalog"]["pool_key"]
            }
        ),
        "avoid_list": artifacts.catalog_json["avoid_list"],
        "row_count": len(artifacts.catalog_json["providers"]),
    }
    (REPO_ROOT / "config" / "gateway-catalog.lock.json").write_text(
        json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sync the free-tier catalog from a pinned OmniRoute npm release."
    )
    parser.add_argument("--version", required=True)
    parser.add_argument(
        "--from-json", type=Path, help="Skip npm; read an already-extracted JSON (tests, offline)."
    )
    args = parser.parse_args(argv)
    if args.from_json is not None:
        payload = json.loads(args.from_json.read_text(encoding="utf-8"))
        tarball_sha = hashlib.sha256(args.from_json.read_bytes()).hexdigest()
    else:
        with tempfile.TemporaryDirectory() as prefix:
            payload = extract_from_npm(args.version, Path(prefix))
            tarballs = list(Path(prefix).glob("**/omniroute-*.tgz"))
            tarball_sha = (
                hashlib.sha256(tarballs[0].read_bytes()).hexdigest() if tarballs else "unavailable"
            )
    rows = load_rows(payload["budgets"], payload["endpoints"])
    write_artifacts(
        transform(rows, curated_at=payload["curatedAt"]),
        version=args.version,
        tarball_sha256=tarball_sha,
    )
    print(f"synced {len(rows)} rows from omniroute@{args.version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [x] **Step 5: Implement `check_catalog_drift.py`**

```python
"""CI drift gate: any avoid-list change fails; count changes warn (§8.2)."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class DriftReport:
    hard_failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def compare(current: Mapping[str, Any], locked: Mapping[str, Any]) -> DriftReport:
    report = DriftReport()
    added = sorted(set(current["avoid_list"]) - set(locked["avoid_list"]))
    removed = sorted(set(locked["avoid_list"]) - set(current["avoid_list"]))
    if added or removed:
        report.hard_failures.append(f"avoid-list changed: +{added} -{removed}")
    for key in ("row_count", "steady_monthly", "pool_count"):
        if current[key] != locked[key]:
            report.warnings.append(f"{key} {locked[key]} -> {current[key]}")
    return report


def main() -> int:
    catalog = json.loads(
        (REPO_ROOT / "config" / "gateway-catalog.json").read_text(encoding="utf-8")
    )
    lock = json.loads(
        (REPO_ROOT / "config" / "gateway-catalog.lock.json").read_text(encoding="utf-8")
    )
    current = {
        "avoid_list": catalog["avoid_list"],
        "row_count": len(catalog["providers"]),
        "steady_monthly": catalog["totals"]["steady_monthly"],
        "pool_count": lock["pool_count"],
    }
    report = compare(current, lock)
    for line in report.warnings:
        print(f"::warning::gateway catalog {line}")
    for line in report.hard_failures:
        print(f"::error::gateway catalog {line}")
    return 1 if report.hard_failures else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [x] **Step 6: Run tests to verify they pass**

Run: `uv run --frozen pytest tests/tools/test_gateway_sync_catalog.py tests/tools/test_gateway_catalog_drift.py -q`
Expected: 5 passed.

- [x] **Step 7: Produce the real artifacts from the pinned release**

Run: `uv run --frozen python tools/gateway/sync_catalog.py --version 3.8.51`
Expected: `synced 444 rows from omniroute@3.8.51`; `seed/gateway-providers.yaml`, `seed/gateway-capabilities.yaml`, `config/gateway-catalog.json`, `config/gateway-catalog.lock.json` written. Then `uv run --frozen python tools/gateway/check_catalog_drift.py` → exit 0.

Then verify the seeds load hermetically: `uv run --frozen pytest tests/test_gateway_seed.py tests/test_seed.py -q` (Task 2's loader) and `uv run --frozen pitwall seed seed/gateway-capabilities.yaml seed/gateway-providers.yaml --dry-run` if the CLI supports it; otherwise `uv run --frozen python -c "from pitwall.seed import load_seed_documents; print(len(load_seed_documents(['seed/gateway-providers.yaml'])))"` → `1`.

- [x] **Step 8: Provenance and attribution records**

`docs/provenance/gateway-catalog-2026-09-10.md`:

```markdown
# Free-tier catalog sync provenance — 2026-09-10

- Upstream: `omniroute@3.8.51` (npm), MIT, © 2026 diegosouzapw
- Tarball SHA-256: <from config/gateway-catalog.lock.json:tarball_sha256>
- Extractor: `tools/gateway/sync_catalog.py`, SHA-256 <lock:extractor_sha256>
- Rows: 444; steady monthly tokens (pool-deduped): <lock:steady_monthly>; pools: <lock:pool_count>
- Avoid-list providers: <lock:avoid_list>
- Diff summary: initial import.
```

`docs/legal/gateway-attribution-review.md` records that only catalog data (facts, not expression beyond display names) and, after Task 18, the `open-sse/` subset are shipped, with the MIT notice chain and the dropped `wreq-js` notice burden.

- [x] **Step 9: Add the CI job**

In `.github/workflows/ci.yml` after the `docs` job:

```yaml
  gateway-catalog-drift:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
      - run: uv sync --frozen --extra dev
      - name: Free-tier catalog drift gate
        run: uv run --frozen python tools/gateway/check_catalog_drift.py
```

Run: `uv run --frozen python tools/ci/check_workflows.py` → exit 0.

- [x] **Step 10: Commit**

```bash
git add tools/gateway seed/gateway-capabilities.yaml seed/gateway-providers.yaml config/gateway-catalog.json config/gateway-catalog.lock.json docs/provenance/gateway-catalog-2026-09-10.md docs/legal/gateway-attribution-review.md .github/workflows/ci.yml tests/tools/test_gateway_sync_catalog.py tests/tools/test_gateway_catalog_drift.py tests/tools/fixtures/mini-catalog.json
git commit -s -m "feat(gateway): sync the free-tier catalog into seeds and config with a drift gate"
```

---

### Task 4: `providers/gateway.py` — the `openai_gateway` adapter

**Files:**
- Create: `src/pitwall/providers/gateway.py`
- Modify: `src/pitwall/providers/registry.py:222-236` (`create_default_registry`), `src/pitwall/providers/__init__.py` (export)
- Test: `tests/providers/test_gateway_provider.py`

**Interfaces:**
- Consumes: `ProviderAdapterId.GATEWAY`, `default_credential_reference` (Task 2); `resolve_adapter_credentials`, `InferenceRequest`, `AvailabilityRequest`, `AvailabilityItem`, `AvailabilityKind.MODEL` (`src/pitwall/providers/interface.py`); `ZeroOrEnergyPricing`, `PerTokenPricing`, `parse_pricing_model` (`src/pitwall/cost/estimator.py`).
- Produces: `GatewayProvider` (`id = "openai_gateway"`, capabilities `{SYNC_INFERENCE, AVAILABILITY}`), `GatewayCredentials(api_key: SecretStr | None = None, base_url, timeout_s)`, `QuotaExhausted(GatewayProviderError)` with `.reset_at: datetime | None` and `.reason: Literal["quota_exhausted","rate_limit_exceeded","model_capacity"]`, `GatewayInferenceResult(InferenceResult)` with `prompt_tokens/completion_tokens/total_tokens`, and module function `classify_429(status: int, body: str, headers: Mapping[str,str], *, now) -> tuple[reason, reset_at | None]`.

- [x] **Step 1: Write the failing tests**

```python
"""Gateway adapter: keyless auth, 429 → QuotaExhausted, 5xx → provider error, availability from catalog."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import httpx
import pytest

from pitwall.core.enums import (
    CapabilityClass,
    CapabilitySource,
    CostMode,
    ProviderAdapterId,
    ProviderType,
)
from pitwall.core.models import Capability
from pitwall.core.models import Provider as ProviderRecord
from pitwall.cost.estimator import PerTokenPricing, ZeroOrEnergyPricing
from pitwall.providers.interface import (
    AvailabilityKind,
    AvailabilityRequest,
    CredentialReference,
    InferenceRequest,
    ProviderOperationContext,
)
from pitwall.providers.gateway import (
    GatewayProvider,
    GatewayProviderError,
    QuotaExhausted,
    classify_429,
)

NOW = dt.datetime(2026, 9, 10, 12, 0, tzinfo=dt.UTC)


def _capability(cost_mode: str = "zero") -> Capability:
    return Capability(
        id="cap_coding",
        name="coding.chat",
        version="1.0.0",
        class_=CapabilityClass.LLM,
        cost_mode=CostMode(cost_mode),
        source=CapabilitySource.YAML,
        created_at=NOW,
        updated_at=NOW,
    )


def _provider(cost: dict | None = None, **catalog) -> ProviderRecord:
    return ProviderRecord(
        id="prov_gw",
        capability_id="cap_coding",
        name="gw-beta-b1",
        adapter_id=ProviderAdapterId.GATEWAY,
        provider_type=ProviderType.OPENAI_GATEWAY,
        priority=50,
        updated_at=NOW,
        config={
            "cost": cost or {"mode": "zero"},
            "openai_base_url": "http://127.0.0.1:20130/v1",
            "gateway": {
                "base_url": "http://127.0.0.1:20130/v1",
                "model_id": "beta/b1",
                "catalog": {
                    "free_type": "keyless",
                    "tos": "ok",
                    "hard_stop_guaranteed": True,
                    **catalog,
                },
            },
        },
    )


def _request(transport_handler, env: dict[str, str]) -> tuple[GatewayProvider, InferenceRequest]:
    provider = GatewayProvider(transport=httpx.MockTransport(transport_handler), environ=env)
    request = InferenceRequest(
        context=ProviderOperationContext(pool=None, now=NOW),
        capability=_capability(),
        provider_record=_provider(),
        credentials=CredentialReference("PITWALL_GATEWAY_API_KEY"),
        payload={"messages": [{"role": "user", "content": "hi"}]},
    )
    return provider, request


@pytest.mark.anyio
async def test_keyless_infer_sends_no_authorization_header_when_env_is_unset() -> None:
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(req.headers)
        body = json.loads(req.content)
        assert body["model"] == "beta/b1"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}}],
                "model": "beta/b1",
                "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
            },
        )

    provider, request = _request(handler, env={})
    result = await provider.infer(request)
    assert "authorization" not in seen
    assert result.prompt_tokens == 3 and result.completion_tokens == 1
    assert result.provider_id == "prov_gw"


@pytest.mark.anyio
async def test_keyed_infer_sends_bearer_from_env() -> None:
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(req.headers)
        return httpx.Response(200, json={"choices": [], "model": "beta/b1"})

    provider, request = _request(handler, env={"PITWALL_GATEWAY_API_KEY": "sk-test"})
    await provider.infer(request)
    assert seen["authorization"] == "Bearer sk-test"


@pytest.mark.anyio
async def test_429_with_reset_header_maps_to_quota_exhausted_with_reset_at() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"x-ratelimit-reset": "2026-09-10T13:00:00Z"},
            json={"error": {"message": "daily quota exceeded"}},
        )

    provider, request = _request(handler, env={})
    with pytest.raises(QuotaExhausted) as excinfo:
        await provider.infer(request)
    assert excinfo.value.reason == "quota_exhausted"
    assert excinfo.value.reset_at == dt.datetime(2026, 9, 10, 13, 0, tzinfo=dt.UTC)


@pytest.mark.anyio
async def test_5xx_raises_plain_provider_error_for_the_cooldown_path() -> None:
    provider, request = _request(lambda req: httpx.Response(503, text="overloaded"), env={})
    with pytest.raises(GatewayProviderError) as excinfo:
        await provider.infer(request)
    assert not isinstance(excinfo.value, QuotaExhausted)
    assert excinfo.value.status_code == 503


def test_pricing_model_follows_the_provider_row_not_the_adapter() -> None:
    provider = GatewayProvider()
    assert isinstance(provider.pricing_model(_capability("zero"), _provider()), ZeroOrEnergyPricing)
    metered = _provider(
        cost={"mode": "per_token", "per_million_input": "0.27", "per_million_output": "1.10"}
    )
    assert isinstance(provider.pricing_model(_capability("per_token"), metered), PerTokenPricing)


@pytest.mark.anyio
async def test_availability_is_read_from_catalog_evidence_without_egress() -> None:
    def handler(req: httpx.Request) -> httpx.Response:  # any egress is a test failure
        raise AssertionError("availability must not call upstream")

    provider = GatewayProvider(transport=httpx.MockTransport(handler), environ={})
    result = await provider.availability(
        AvailabilityRequest(
            context=ProviderOperationContext(pool=None, now=NOW),
            provider_record=_provider(trains_on_prompts=True),
            credentials=CredentialReference("PITWALL_GATEWAY_API_KEY"),
        )
    )
    [item] = result.items
    assert item.kind == AvailabilityKind.MODEL and item.resource_id == "beta/b1"
    assert item.attributes["trains_on_prompts"] is True
    assert result.source_contract == "gateway-catalog-2026-09-10"


def test_classify_429_reads_retry_after_seconds_and_permanent_ban() -> None:
    reason, reset = classify_429(429, "please retry in 30s", {}, now=NOW)
    assert (reason, reset) == ("rate_limit_exceeded", NOW + dt.timedelta(seconds=30))
    reason, reset = classify_429(429, "account has been deactivated", {}, now=NOW)
    assert (reason, reset) == ("permanent_ban", None)
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen pytest tests/providers/test_gateway_provider.py -q`
Expected: FAIL — `ModuleNotFoundError: pitwall.providers.gateway`.

- [x] **Step 3: Implement the adapter**

```python
"""OpenAI-compatible gateway adapter for free and cheap pools (research §9.1)."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Annotated, Any, Literal

import httpx
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, SecretStr

from pitwall.core.enums import CostMode
from pitwall.core.models import Capability
from pitwall.core.models import Provider as ProviderRecord
from pitwall.cost.estimator import (
    PerTokenPricing,
    TaggedPricingModel,
    ZeroOrEnergyPricing,
    parse_pricing_model,
)
from pitwall.providers.interface import (
    AvailabilityItem,
    AvailabilityKind,
    AvailabilityRequest,
    AvailabilityResult,
    CredentialInput,
    CredentialReference,
    InferenceRequest,
    InferenceResult,
    ProviderCapability,
)
from pitwall.resolver.provider_urls import validate_openai_base_url

CATALOG_SOURCE_CONTRACT = "gateway-catalog-2026-09-10"
_CHAT_COMPLETIONS_PATH = "chat/completions"
_MAX_ERROR_BODY_CHARS = 500
QuotaReason = Literal["quota_exhausted", "rate_limit_exceeded", "model_capacity", "permanent_ban"]

# Duration semantics ported from OmniRoute accountFallback.ts (retry-after regexes) and
# constants.ts RateLimitReason; permanent signals are terminal, never auto-cleared.
_RETRY_IN_S = re.compile(r"retry (?:in|after)\s+([\d.]+)\s*s", re.I)
_RESET_AT_ISO = re.compile(
    r"(?:reset(?:s)? at|try again at|available at)\s+(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?Z?)",
    re.I,
)
_DAILY = re.compile(r"\b(daily|per day|today)\b", re.I)
_MONTHLY = re.compile(r"\b(monthly|this month|quota)\b", re.I)
_PERMANENT = (
    "account has been deactivated",
    "account suspended",
    "permanently banned",
    "access revoked",
)

SafeGatewayUrl = Annotated[str, AfterValidator(validate_openai_base_url)]


class GatewayCredentials(BaseModel):
    """Bearer key is optional: keyless pools carry no credential at all."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    api_key: SecretStr | None = None
    base_url: SafeGatewayUrl = "http://127.0.0.1:20130/v1"
    timeout_s: float = Field(default=120.0, gt=0)


class GatewayProviderError(RuntimeError):
    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        super().__init__(
            f"gateway request failed with HTTP {status_code}: {body[:_MAX_ERROR_BODY_CHARS]}"
        )


class QuotaExhausted(GatewayProviderError):
    """Typed 429 signal consumed by routing/lockout.py."""

    def __init__(
        self, status_code: int, body: str, *, reason: QuotaReason, reset_at: dt.datetime | None
    ) -> None:
        super().__init__(status_code, body)
        self.reason = reason
        self.reset_at = reset_at


@dataclass(frozen=True, slots=True, kw_only=True)
class GatewayInferenceResult(InferenceResult):
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    raw: Mapping[str, Any] = field(default_factory=dict)


def classify_429(
    status: int, body: str, headers: Mapping[str, str], *, now: dt.datetime
) -> tuple[QuotaReason, dt.datetime | None]:
    text = body.lower()
    if any(signal in text for signal in _PERMANENT):
        return "permanent_ban", None
    reset_header = headers.get("x-ratelimit-reset") or headers.get("retry-after")
    if reset_header:
        try:
            return "quota_exhausted", dt.datetime.fromisoformat(reset_header.replace("Z", "+00:00"))
        except ValueError:
            if reset_header.isdigit():
                return "rate_limit_exceeded", now + dt.timedelta(seconds=int(reset_header))
    if match := _RETRY_IN_S.search(body):
        return "rate_limit_exceeded", now + dt.timedelta(seconds=float(match.group(1)))
    if match := _RESET_AT_ISO.search(body):
        return "quota_exhausted", dt.datetime.fromisoformat(
            match.group(1).replace("Z", "+00:00")
        ).astimezone(dt.UTC)
    if _DAILY.search(body):
        return "quota_exhausted", (now + dt.timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    if _MONTHLY.search(body):
        first_next = (now.replace(day=1) + dt.timedelta(days=32)).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        return "quota_exhausted", first_next
    if status in (503, 529):
        return "model_capacity", None
    return "rate_limit_exceeded", None


class GatewayProvider:
    id = "openai_gateway"
    name = "OpenAI-compatible gateway"
    credential_schema = GatewayCredentials
    capabilities = frozenset({ProviderCapability.SYNC_INFERENCE, ProviderCapability.AVAILABILITY})

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._transport = transport
        self._environ = os.environ if environ is None else environ

    def pricing_model(
        self, capability: Capability, provider_record: ProviderRecord
    ) -> TaggedPricingModel:
        mode = CostMode(capability.cost_mode)
        pricing = parse_pricing_model(provider_record, cost_mode=mode)
        if not isinstance(pricing, (ZeroOrEnergyPricing, PerTokenPricing)):
            raise ValueError("GatewayProvider requires zero or per_token pricing")
        return pricing

    def _resolve(
        self, credentials: CredentialInput, provider_record: ProviderRecord
    ) -> GatewayCredentials:
        base_url = str(_gateway_config(provider_record)["base_url"])
        if isinstance(credentials, CredentialReference):
            value = self._environ.get(credentials.name, "")
            return GatewayCredentials(
                api_key=SecretStr(value) if value else None, base_url=base_url
            )
        if isinstance(credentials, GatewayCredentials):
            return credentials
        return GatewayCredentials.model_validate({**dict(credentials), "base_url": base_url})

    async def infer(self, request: InferenceRequest) -> GatewayInferenceResult:
        creds = self._resolve(request.credentials, request.provider_record)
        model_id = str(_gateway_config(request.provider_record)["model_id"])
        body = {**dict(request.payload), "model": model_id}
        headers = {"Content-Type": "application/json"}
        if creds.api_key is not None:
            headers["Authorization"] = f"Bearer {creds.api_key.get_secret_value()}"
        async with httpx.AsyncClient(
            base_url=creds.base_url, timeout=creds.timeout_s, transport=self._transport
        ) as client:
            response = await client.post(_CHAT_COMPLETIONS_PATH, headers=headers, json=body)
        now = request.context.now or dt.datetime.now(dt.UTC)
        if response.status_code == 429 or (
            response.status_code in (503, 529) and "capacity" in response.text.lower()
        ):
            reason, reset_at = classify_429(
                response.status_code, response.text, dict(response.headers), now=now
            )
            raise QuotaExhausted(
                response.status_code, response.text, reason=reason, reset_at=reset_at
            )
        if response.status_code >= 400:
            raise GatewayProviderError(response.status_code, response.text)
        try:
            data = json.loads(response.text, parse_float=Decimal)
        except json.JSONDecodeError as exc:
            raise GatewayProviderError(response.status_code, "response body was not JSON") from exc
        if not isinstance(data, Mapping):
            raise GatewayProviderError(response.status_code, "response body was not a JSON object")
        usage = data.get("usage") if isinstance(data.get("usage"), Mapping) else {}
        return GatewayInferenceResult(
            provider_id=request.provider_record.id,
            output=data,
            raw=data,
            model=str(data.get("model", model_id)),
            prompt_tokens=_opt_int(usage.get("prompt_tokens")),
            completion_tokens=_opt_int(usage.get("completion_tokens")),
            total_tokens=_opt_int(usage.get("total_tokens")),
        )

    async def availability(self, request: AvailabilityRequest) -> AvailabilityResult:
        """Catalog evidence is the availability truth; no upstream call (§9.1)."""
        gateway = _gateway_config(request.provider_record)
        catalog = dict(gateway.get("catalog", {}))
        observed_at = (request.context.now or dt.datetime.now(dt.UTC)).astimezone(dt.UTC)
        item = AvailabilityItem(
            provider_id=request.provider_record.id,
            resource_id=str(gateway["model_id"]),
            kind=AvailabilityKind.MODEL,
            available=bool(request.provider_record.enabled),
            attributes={
                k: catalog.get(k)
                for k in (
                    "free_type",
                    "tos",
                    "trains_on_prompts",
                    "hard_stop_guaranteed",
                    "pool_key",
                    "eligibility_gate",
                    "monthly_tokens",
                    "credit_tokens",
                    "display_name",
                )
            },
        )
        return AvailabilityResult(
            provider_id=request.provider_record.id,
            observed_at=observed_at,
            source_contract=CATALOG_SOURCE_CONTRACT,
            items=(item,),
        )


def _gateway_config(provider_record: ProviderRecord) -> Mapping[str, Any]:
    gateway = (
        provider_record.config.get("gateway")
        if isinstance(provider_record.config, Mapping)
        else None
    )
    if (
        not isinstance(gateway, Mapping)
        or not gateway.get("base_url")
        or not gateway.get("model_id")
    ):
        raise ValueError("openai_gateway provider requires config.gateway.base_url and model_id")
    return gateway


def _opt_int(value: object) -> int | None:
    return int(value) if isinstance(value, (int, Decimal)) and not isinstance(value, bool) else None
```

`validate_openai_base_url` is the existing SSRF validator behind `openai_base_url` in `src/pitwall/resolver/provider_urls.py` (the function that public-endpoint config validation calls); if it is a private helper there, expose it under that name in the same module.

- [x] **Step 4: Register the adapter**

In `src/pitwall/providers/registry.py` `create_default_registry`, add `from pitwall.providers.gateway import GatewayProvider` and `registry.register(GatewayProvider())` after `LambdaCloudProvider()`. Update `tests/providers/test_core_contracts.py`'s registered-id assertion to include `"openai_gateway"` and `docs/sdlc/20-provider-plugins.md:20` list.

- [x] **Step 5: Run tests to verify they pass**

Run: `uv run --frozen pytest tests/providers -q`
Expected: all PASS (7 new).

- [x] **Step 6: Security lane**

Run: `uv run --frozen pytest -q -m security tests/providers tests/security 2>&1 | tail -2`
Expected: passed; add to `tests/security/test_gateway_credentials.py`:

```python
def test_gateway_keyless_never_reads_env_when_key_absent(monkeypatch) -> None:
    from pitwall.providers.gateway import GatewayProvider

    provider = GatewayProvider(environ={})
    creds = provider._resolve(CredentialReference("PITWALL_GATEWAY_API_KEY"), _provider())
    assert creds.api_key is None


def test_gateway_base_url_rejects_non_loopback_http() -> None:
    with pytest.raises(ValueError):
        GatewayCredentials(base_url="http://10.0.0.5/v1")
```

- [x] **Step 7: Commit**

```bash
git add src/pitwall/providers/gateway.py src/pitwall/providers/registry.py src/pitwall/providers/__init__.py src/pitwall/resolver/provider_urls.py tests/providers/test_gateway_provider.py tests/providers/test_core_contracts.py tests/security/test_gateway_credentials.py docs/sdlc/20-provider-plugins.md
git commit -s -m "feat(providers): openai_gateway adapter with keyless auth and typed quota signals"
```

---

### Task 5: Quota repository, `QuotaSnapshot` in `PlanningContext`, Stage-2 gate and Stage-3 terms

**Files:**
- Create: `src/pitwall/db/quota_repository.py`, `src/pitwall/routing/quota.py`
- Modify: `src/pitwall/routing/context.py:97-175` (`PlanningContext`), `src/pitwall/routing/types.py:120-160` (`ScoreExplanation`, `ProviderEliminated`), `src/pitwall/routing/scoring.py:34-86` (`explain_score`), `src/pitwall/routing/planner.py:305-322` (`_stage2_elimination_reasons`), `src/pitwall/routing/production.py:402-418` (`build_production_plan`) and `:1336-1372` (`_plan_safe_payload`), `src/pitwall/config.py:437-445` (`pitwall_routing_weights` keys), `src/pitwall/routing/__init__.py` (`__all__`)
- Test: `tests/db/test_quota_repository.py`, `tests/routing/test_quota_gate.py`, `tests/routing/test_quota_scoring.py`, `tests/routing/test_planning_context.py` (extend)

**Interfaces:**
- Consumes: tables from Task 2; `Provider.config["gateway"]["catalog"]` shape from Task 3; `parse_pricing_model(...).kind`.
- Produces:
  - `QuotaRecord(provider_id, pool_key, free_type, window_start, reset_at, budget_units: Decimal | None, used_units: Decimal, tos_verdict, evidence: Mapping, updated_at)` (frozen dataclass).
  - `QuotaSnapshot(records: tuple[QuotaRecord, ...])` with `.get(provider_id, pool_key) -> QuotaRecord | None`, `.to_dict()`, `.from_dict()`, `.empty()`.
  - `QuotaRepository(pool)`: `async list_all() -> tuple[QuotaRecord, ...]`, `async upsert(record)`, `async record_sample(provider_id, sampled_at, used_units, reset_at)`, `async add_usage(provider_id, pool_key, units: Decimal)`, `async list_model_ids() -> tuple[ModelIdMapping, ...]`, `async upsert_model_id(model_id, capability, provider)`.
  - `quota_eligible(provider, snapshot, now) -> tuple[bool, str | None]` and `quota_score_terms(provider, snapshot, now) -> QuotaTerms(headroom: float, reset_proximity: float)`.
  - `PlanningContext.quota_snapshot: QuotaSnapshot` (default empty) included in the plan-id hash body.
  - `ProviderEliminated.QUOTA_INELIGIBLE = "quota_ineligible"`; `ScoreExplanation.quota_headroom_bonus`, `ScoreExplanation.reset_proximity_bonus`; weights `w_quota` (10.0) and `w_reset` (2.5) resolvable through `routing_weights_for`.

- [x] **Step 1: Write the failing tests**

`tests/routing/test_quota_gate.py` (the §9.3 truth table, every branch):

```python
import datetime as dt
from decimal import Decimal

import pytest

from pitwall.core.enums import ProviderAdapterId, ProviderType
from pitwall.core.models import Provider
from pitwall.routing.quota import QuotaRecord, QuotaSnapshot, quota_eligible, quota_score_terms

NOW = dt.datetime(2026, 9, 10, 12, 0, tzinfo=dt.UTC)


def _provider(mode: str = "zero", pool_key: str | None = "alpha-pool") -> Provider:
    return Provider(
        id="prov_gw",
        capability_id="cap",
        name="gw",
        adapter_id=ProviderAdapterId.GATEWAY,
        provider_type=ProviderType.OPENAI_GATEWAY,
        priority=50,
        updated_at=NOW,
        config={
            "cost": {"mode": mode}
            | ({} if mode == "zero" else {"per_million_input": "0.1", "per_million_output": "0.2"}),
            "gateway": {
                "base_url": "http://127.0.0.1:20130/v1",
                "model_id": "m",
                "catalog": {
                    "pool_key": pool_key,
                    "free_type": "recurring-monthly",
                    "tos": "ok",
                    "hard_stop_guaranteed": True,
                },
            },
        },
    )


def _record(**overrides) -> QuotaRecord:
    base = dict(
        provider_id="prov_gw",
        pool_key="alpha-pool",
        free_type="recurring-monthly",
        window_start=NOW - dt.timedelta(days=10),
        reset_at=NOW + dt.timedelta(days=20),
        budget_units=Decimal("5000000"),
        used_units=Decimal("1000000"),
        tos_verdict="ok",
        evidence={},
        updated_at=NOW,
    )
    base.update(overrides)
    return QuotaRecord(**base)


@pytest.mark.parametrize(
    ("provider", "record", "expected"),
    [
        (_provider("per_token"), None, (True, None)),  # metered: budget gate owns it
        (_provider(), None, (False, "zero-priced without catalog evidence")),  # fail closed
        (_provider(), _record(tos_verdict="avoid"), (False, "tos-avoid")),
        (_provider(), _record(used_units=Decimal("5000000")), (False, "quota-exhausted")),
        (_provider(), _record(), (True, None)),
        (
            _provider(),
            _record(budget_units=None, free_type="keyless"),
            (True, None),
        ),  # uncapped keyless
    ],
)
def test_quota_eligible_truth_table(provider, record, expected) -> None:
    snapshot = QuotaSnapshot(records=(record,) if record else ())
    assert quota_eligible(provider, snapshot, NOW) == expected


def test_score_terms_are_pure_and_bounded() -> None:
    snapshot = QuotaSnapshot(records=(_record(),))
    terms = quota_score_terms(_provider(), snapshot, NOW)
    assert terms.headroom == pytest.approx(0.8)  # (5M - 1M) / 5M
    assert terms.reset_proximity == pytest.approx(1 / 3)  # 10 of 30 days elapsed
    assert quota_score_terms(_provider("per_token"), snapshot, NOW) == quota_score_terms(
        _provider("per_token"), QuotaSnapshot.empty(), NOW
    )


def test_headroom_is_monotone_in_used_units() -> None:
    last = 2.0
    for used in range(0, 5_000_001, 500_000):
        snapshot = QuotaSnapshot(records=(_record(used_units=Decimal(used)),))
        headroom = quota_score_terms(_provider(), snapshot, NOW).headroom
        assert headroom <= last
        last = headroom
```

`tests/routing/test_quota_scoring.py`:

```python
def test_explain_score_carries_quota_terms_weighted_by_settings() -> None:
    from pitwall.routing.scoring import explain_score
    from pitwall.routing.quota import QuotaSnapshot

    snapshot = QuotaSnapshot(records=(_record(),))
    explanation = explain_score(
        _provider(), quota_snapshot=snapshot, now=NOW, w_quota=10.0, w_reset=2.5
    )
    assert explanation.quota_headroom_bonus == pytest.approx(8.0)
    assert explanation.reset_proximity_bonus == pytest.approx(2.5 / 3)
    assert explanation.to_dict()["quota_headroom_bonus"] == pytest.approx(8.0)
    baseline = explain_score(_provider(), now=NOW)
    assert baseline.quota_headroom_bonus == 0.0 and baseline.final_score < explanation.final_score
```

Extend `tests/routing/test_planning_context.py`:

```python
def test_replay_with_different_quota_snapshot_changes_plan_id() -> None:
    a = PlanningContext.replay(
        now=NOW,
        providers=[_provider()],
        capability=_capability(),
        quota_snapshot=QuotaSnapshot(records=(_record(),)),
    )
    b = PlanningContext.replay(
        now=NOW,
        providers=[_provider()],
        capability=_capability(),
        quota_snapshot=QuotaSnapshot(records=(_record(used_units=Decimal("2")),)),
    )
    plan_a = build_production_plan(
        capability=_capability(),
        providers=[_provider()],
        payload={},
        operation=RoutingOperation.SYNC_INFERENCE,
        registry=_registry(),
        now=NOW,
        mode="weighted",
        weights=_weights(),
        max_attempts=1,
        context=a,
    )
    plan_b = build_production_plan(**{**_plan_kwargs(), "context": b})
    assert plan_a.plan_id != plan_b.plan_id
    assert (
        build_production_plan(**{**_plan_kwargs(), "context": a}).plan_id == plan_a.plan_id
    )  # byte-replayable
```

(`_plan_kwargs`, `_registry`, `_weights` are small local helpers in that test module; write them beside the existing `_capability`/`_provider` helpers there.)

`tests/db/test_quota_repository.py` follows `tests/db/test_workload_repository.py`'s `make_asyncpg_pool(fetchrow=…)` pattern and asserts the SQL contains `INSERT INTO pitwall.provider_quotas` with `ON CONFLICT (provider_id, pool_key) DO UPDATE`, and that `add_usage` runs `used_units = (used_units::numeric + $3)::text`.

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen pytest tests/routing/test_quota_gate.py tests/routing/test_quota_scoring.py tests/db/test_quota_repository.py -q`
Expected: FAIL — `ModuleNotFoundError: pitwall.routing.quota`.

- [x] **Step 3: Implement `routing/quota.py`**

```python
"""Quota evidence snapshot plus Stage-2 gate and Stage-3 terms (research §9.3)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from pitwall.cost.estimator import parse_pricing_model


@dataclass(frozen=True, slots=True)
class QuotaRecord:
    provider_id: str
    pool_key: str
    free_type: str
    window_start: dt.datetime | None
    reset_at: dt.datetime | None
    budget_units: Decimal | None
    used_units: Decimal
    tos_verdict: str
    evidence: Mapping[str, Any] = field(default_factory=dict)
    updated_at: dt.datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "pool_key": self.pool_key,
            "free_type": self.free_type,
            "window_start": self.window_start.isoformat() if self.window_start else None,
            "reset_at": self.reset_at.isoformat() if self.reset_at else None,
            "budget_units": str(self.budget_units) if self.budget_units is not None else None,
            "used_units": str(self.used_units),
            "tos_verdict": self.tos_verdict,
            "evidence": dict(self.evidence),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> QuotaRecord:
        parse = lambda v: dt.datetime.fromisoformat(v) if v else None  # noqa: E731
        return cls(
            provider_id=raw["provider_id"],
            pool_key=raw.get("pool_key") or "",
            free_type=raw["free_type"],
            window_start=parse(raw.get("window_start")),
            reset_at=parse(raw.get("reset_at")),
            budget_units=Decimal(raw["budget_units"])
            if raw.get("budget_units") is not None
            else None,
            used_units=Decimal(raw.get("used_units", "0")),
            tos_verdict=raw["tos_verdict"],
            evidence=dict(raw.get("evidence", {})),
        )


@dataclass(frozen=True, slots=True)
class QuotaSnapshot:
    """Immutable quota view captured at planning time; part of the plan identity."""

    records: tuple[QuotaRecord, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "records", tuple(sorted(self.records, key=lambda r: (r.provider_id, r.pool_key)))
        )

    @classmethod
    def empty(cls) -> QuotaSnapshot:
        return cls()

    def get(self, provider_id: str, pool_key: str | None) -> QuotaRecord | None:
        key = pool_key or ""
        for record in self.records:
            if record.provider_id == provider_id and record.pool_key == key:
                return record
        return None

    def to_dict(self) -> dict[str, Any]:
        return {"records": [r.to_dict() for r in self.records]}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> QuotaSnapshot:
        return cls(records=tuple(QuotaRecord.from_dict(r) for r in raw.get("records", [])))


@dataclass(frozen=True, slots=True)
class QuotaTerms:
    headroom: float = 0.0
    reset_proximity: float = 0.0


def _pricing_kind(provider: Any) -> str:
    try:
        return parse_pricing_model(provider).kind
    except TypeError, ValueError:
        return "unknown"


def _pool_key(provider: Any) -> str | None:
    config = getattr(provider, "config", None) or (
        provider.get("config") if isinstance(provider, Mapping) else {}
    )
    gateway = config.get("gateway", {}) if isinstance(config, Mapping) else {}
    catalog = gateway.get("catalog", {}) if isinstance(gateway, Mapping) else {}
    value = catalog.get("pool_key")
    return str(value) if value else None


def _provider_id(provider: Any) -> str:
    return str(getattr(provider, "id", None) or provider["id"])


def quota_eligible(
    provider: Any, snapshot: QuotaSnapshot, now: dt.datetime
) -> tuple[bool, str | None]:
    if _pricing_kind(provider) != "zero":
        return True, None
    record = snapshot.get(_provider_id(provider), _pool_key(provider))
    if record is None:
        return False, "zero-priced without catalog evidence"
    if record.tos_verdict == "avoid":
        return False, "tos-avoid"
    if record.reset_at is not None and record.reset_at <= now and record.budget_units is not None:
        return True, None  # window rolled; the poller will re-zero used_units on its next tick
    if record.budget_units is not None and record.used_units >= record.budget_units:
        return False, "quota-exhausted"
    return True, None


def quota_score_terms(provider: Any, snapshot: QuotaSnapshot, now: dt.datetime) -> QuotaTerms:
    if _pricing_kind(provider) != "zero":
        return QuotaTerms()
    record = snapshot.get(_provider_id(provider), _pool_key(provider))
    if record is None:
        return QuotaTerms()
    headroom = 1.0
    if record.budget_units is not None and record.budget_units > 0:
        headroom = max(
            0.0, min(1.0, float((record.budget_units - record.used_units) / record.budget_units))
        )
    proximity = 0.0
    if (
        record.window_start is not None
        and record.reset_at is not None
        and record.reset_at > record.window_start
    ):
        window_min = (record.reset_at - record.window_start).total_seconds() / 60
        to_reset_min = max(0.0, (record.reset_at - now).total_seconds() / 60)
        proximity = max(0.0, min(1.0, 1 - to_reset_min / window_min))
    return QuotaTerms(headroom=headroom, reset_proximity=proximity)
```

- [x] **Step 4: Thread the snapshot through context, scoring, planner, production**

`routing/context.py` `PlanningContext`: add field `quota_snapshot: QuotaSnapshot = field(default_factory=QuotaSnapshot.empty)`; `live(...)` and `replay(...)` gain keyword `quota_snapshot: QuotaSnapshot | None = None`.

`routing/types.py`: `ProviderEliminated.QUOTA_INELIGIBLE = "quota_ineligible"`; `ScoreExplanation` gains `quota_headroom_bonus: float = 0.0`, `reset_proximity_bonus: float = 0.0`, and `quota_reason: str | None = None`, all emitted by `to_dict`.

`routing/scoring.py` `explain_score(provider, hints=None, observed=None, *, quota_snapshot=None, now=None, w_quota=10.0, w_reset=2.5)`: compute `terms = quota_score_terms(provider, quota_snapshot, now)` when both are given, add `quota_headroom_bonus = w_quota * terms.headroom` and `reset_proximity_bonus = w_reset * terms.reset_proximity` into `score_before_multiplier`.

`routing/planner.py` `_stage2_elimination_reasons(provider, *, now, quota_snapshot=None)`: when a snapshot is supplied and `quota_eligible(...)` is `(False, reason)`, append `ProviderEliminated.QUOTA_INELIGIBLE` and record `reason` in the elimination detail. `_candidate_for_provider` passes `quota_snapshot`, `now`, and the weights into `explain_score`.

`routing/production.py`: `build_production_plan(..., context: PlanningContext | None = None)`; the plan-id body at `:613` gains `"quota_snapshot": context.quota_snapshot.to_dict()`; `_plan_safe_payload` reads `await QuotaRepository(self._pool).list_all()` into `QuotaSnapshot` and passes the context. `routing_weights_for` learns keys `w_quota` and `w_reset` with those defaults; `RoutingWeights` gains the two float fields.

`db/quota_repository.py`:

```python
"""Persistence for provider quota evidence and the proxy model-id map (migration 0033)."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import asyncpg

from pitwall.routing.quota import QuotaRecord


@dataclass(frozen=True, slots=True)
class ModelIdMapping:
    model_id: str
    capability: str
    provider: str


class QuotaRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_all(self) -> tuple[QuotaRecord, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM pitwall.provider_quotas ORDER BY provider_id, pool_key"
            )
        return tuple(_record(row) for row in rows)

    async def upsert(self, record: QuotaRecord) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO pitwall.provider_quotas
                    (provider_id, pool_key, free_type, window_start, reset_at, budget_units, used_units,
                     tos_verdict, evidence, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, now())
                ON CONFLICT (provider_id, pool_key) DO UPDATE SET
                    free_type = EXCLUDED.free_type, window_start = EXCLUDED.window_start,
                    reset_at = EXCLUDED.reset_at, budget_units = EXCLUDED.budget_units,
                    used_units = EXCLUDED.used_units, tos_verdict = EXCLUDED.tos_verdict,
                    evidence = EXCLUDED.evidence, updated_at = now()
                """,
                record.provider_id,
                record.pool_key,
                record.free_type,
                record.window_start,
                record.reset_at,
                str(record.budget_units) if record.budget_units is not None else None,
                str(record.used_units),
                record.tos_verdict,
                dict(record.evidence),
            )

    async def add_usage(self, provider_id: str, pool_key: str, units: Decimal) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE pitwall.provider_quotas SET used_units = (used_units::numeric + $3)::text, updated_at = now()"
                " WHERE provider_id = $1 AND pool_key = $2",
                provider_id,
                pool_key,
                units,
            )

    async def record_sample(
        self,
        provider_id: str,
        sampled_at: dt.datetime,
        used_units: Decimal,
        reset_at: dt.datetime | None,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO pitwall.provider_quota_samples (provider_id, sampled_at, used_units, reset_at)"
                " VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING",
                provider_id,
                sampled_at,
                str(used_units),
                reset_at,
            )

    async def list_model_ids(self) -> tuple[ModelIdMapping, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT model_id, capability, provider FROM pitwall.model_id_map ORDER BY model_id"
            )
        return tuple(
            ModelIdMapping(row["model_id"], row["capability"], row["provider"]) for row in rows
        )

    async def upsert_model_id(self, model_id: str, capability: str, provider: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO pitwall.model_id_map (model_id, capability, provider) VALUES ($1, $2, $3)"
                " ON CONFLICT (model_id) DO UPDATE SET capability = EXCLUDED.capability, provider = EXCLUDED.provider",
                model_id,
                capability,
                provider,
            )


def _record(row: Any) -> QuotaRecord:
    return QuotaRecord(
        provider_id=row["provider_id"],
        pool_key=row["pool_key"],
        free_type=row["free_type"],
        window_start=row["window_start"],
        reset_at=row["reset_at"],
        budget_units=Decimal(row["budget_units"]) if row["budget_units"] is not None else None,
        used_units=Decimal(row["used_units"]),
        tos_verdict=row["tos_verdict"],
        evidence=dict(row["evidence"] or {}),
        updated_at=row["updated_at"],
    )
```

Also record quota burn-down on success: in `production.py` `execute_sync_prepared`, after `_mark_sync_completed`, when `candidate.quote.pricing.kind == "zero"` and the result has `total_tokens`, call `await QuotaRepository(self._pool).add_usage(provider.id, pool_key, Decimal(total_tokens))` inside a `try/except Exception` that logs and never fails the workload (mirrors `_usage_derived_actual`'s "provider data never fails delivered output" rule).

- [x] **Step 5: Run tests to verify they pass**

Run: `uv run --frozen pytest tests/routing tests/db/test_quota_repository.py -q`
Expected: all PASS, including the existing replay-identity tests (an empty snapshot hashes identically for every pre-existing plan fixture).

- [x] **Step 6: Commit**

```bash
git add src/pitwall/routing/quota.py src/pitwall/db/quota_repository.py src/pitwall/routing/context.py src/pitwall/routing/types.py src/pitwall/routing/scoring.py src/pitwall/routing/planner.py src/pitwall/routing/production.py src/pitwall/routing/__init__.py src/pitwall/config.py tests/routing/test_quota_gate.py tests/routing/test_quota_scoring.py tests/routing/test_planning_context.py tests/db/test_quota_repository.py
git commit -s -m "feat(routing): quota snapshot in PlanningContext with Stage-2 gate and Stage-3 terms"
```

---

### Task 6: Per-model lockout (`routing/lockout.py`) wired into execution and Stage 2

**Files:**
- Create: `src/pitwall/routing/lockout.py`
- Modify: `src/pitwall/routing/planner.py` (`_stage2_elimination_reasons`), `src/pitwall/routing/types.py` (`ProviderEliminated.MODEL_LOCKED_OUT`), `src/pitwall/routing/production.py:832-870` (failure branch), `src/pitwall/reconciler/__init__.py` (evidence flush in Task 9's job)
- Test: `tests/routing/test_lockout.py`, `tests/routing/test_production_routing.py` (extend)

**Interfaces:**
- Consumes: `QuotaExhausted` (Task 4).
- Produces: `LockoutKey(provider_id, model_id)`, `LockoutState(failures: int, locked_until: datetime | None, reason: str | None, permanent: bool)`, `LockoutTable` (process-wide, `record_failure(key, *, now, reason, reset_at=None)`, `record_success(key, *, now)`, `is_locked(key, *, now) -> bool`, `snapshot() -> dict[str, dict]`, `clear()`), module singleton `get_lockout_table()`, and `backoff_for(failures) -> timedelta == min(120s * 2**(failures-1), 30min)`.

- [x] **Step 1: Write the failing tests**

```python
import datetime as dt

from pitwall.routing.lockout import LockoutKey, LockoutTable, backoff_for

NOW = dt.datetime(2026, 9, 10, 12, 0, tzinfo=dt.UTC)
KEY = LockoutKey("prov_gw", "beta/b1")


def test_backoff_table_matches_spec() -> None:
    assert [backoff_for(n).total_seconds() for n in (1, 2, 3, 4, 5, 10)] == [
        120,
        240,
        480,
        960,
        1800,
        1800,
    ]


def test_quota_exhausted_locks_until_reset_at() -> None:
    table = LockoutTable()
    table.record_failure(
        KEY, now=NOW, reason="quota_exhausted", reset_at=NOW + dt.timedelta(hours=3)
    )
    assert table.is_locked(KEY, now=NOW + dt.timedelta(hours=2, minutes=59))
    assert not table.is_locked(KEY, now=NOW + dt.timedelta(hours=3))


def test_success_halves_failure_count() -> None:
    table = LockoutTable()
    for _ in range(4):
        table.record_failure(KEY, now=NOW, reason="rate_limit_exceeded")
    table.record_success(KEY, now=NOW + dt.timedelta(hours=1))
    assert table.snapshot()["prov_gw/beta/b1"]["failures"] == 2


def test_permanent_ban_is_terminal_and_operator_visible() -> None:
    table = LockoutTable()
    table.record_failure(KEY, now=NOW, reason="permanent_ban")
    table.record_success(KEY, now=NOW + dt.timedelta(days=30))
    assert table.is_locked(KEY, now=NOW + dt.timedelta(days=365))
    assert table.snapshot()["prov_gw/beta/b1"]["permanent"] is True


def test_locked_tuple_is_eliminated_in_stage2(monkeypatch) -> None:
    from pitwall.routing import lockout
    from pitwall.routing.planner import _stage2_elimination_reasons
    from pitwall.routing.types import ProviderEliminated

    table = LockoutTable()
    table.record_failure(KEY, now=NOW, reason="rate_limit_exceeded")
    monkeypatch.setattr(lockout, "get_lockout_table", lambda: table)
    reasons = _stage2_elimination_reasons(_provider(), now=NOW + dt.timedelta(seconds=10))
    assert ProviderEliminated.MODEL_LOCKED_OUT in reasons
```

`_provider()` in this module is the same gateway `Provider` builder as `tests/routing/test_quota_gate.py` (copy it; the two files must not import from each other).

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run --frozen pytest tests/routing/test_lockout.py -q` → FAIL `ModuleNotFoundError`.

- [x] **Step 3: Implement**

```python
"""(provider, model) lockouts mirroring OmniRoute accountFallback semantics (research §9.4)."""

from __future__ import annotations

import datetime as dt
import threading
from dataclasses import dataclass, replace
from typing import Any

_BASE = dt.timedelta(seconds=120)
_CAP = dt.timedelta(minutes=30)


def backoff_for(failures: int) -> dt.timedelta:
    return min(_BASE * (2 ** max(0, failures - 1)), _CAP)


@dataclass(frozen=True, slots=True)
class LockoutKey:
    provider_id: str
    model_id: str

    def __str__(self) -> str:
        return f"{self.provider_id}/{self.model_id}"


@dataclass(frozen=True, slots=True)
class LockoutState:
    failures: int = 0
    locked_until: dt.datetime | None = None
    reason: str | None = None
    permanent: bool = False


class LockoutTable:
    """In-memory authoritative; flushed to provider_quotas.evidence for observability only."""

    def __init__(self) -> None:
        self._states: dict[LockoutKey, LockoutState] = {}
        self._lock = threading.Lock()

    def record_failure(
        self, key: LockoutKey, *, now: dt.datetime, reason: str, reset_at: dt.datetime | None = None
    ) -> LockoutState:
        with self._lock:
            current = self._states.get(key, LockoutState())
            if current.permanent:
                return current
            failures = current.failures + 1
            if reason == "permanent_ban":
                state = LockoutState(
                    failures=failures, locked_until=None, reason=reason, permanent=True
                )
            elif reason == "quota_exhausted" and reset_at is not None:
                state = LockoutState(failures=failures, locked_until=reset_at, reason=reason)
            else:
                state = LockoutState(
                    failures=failures, locked_until=now + backoff_for(failures), reason=reason
                )
            self._states[key] = state
            return state

    def record_success(self, key: LockoutKey, *, now: dt.datetime) -> LockoutState:
        with self._lock:
            current = self._states.get(key, LockoutState())
            if current.permanent:
                return current
            state = replace(current, failures=current.failures // 2, locked_until=None, reason=None)
            self._states[key] = state
            return state

    def is_locked(self, key: LockoutKey, *, now: dt.datetime) -> bool:
        state = self._states.get(key)
        if state is None:
            return False
        if state.permanent:
            return True
        return state.locked_until is not None and now < state.locked_until

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return {
            str(key): {
                "failures": s.failures,
                "locked_until": s.locked_until.isoformat() if s.locked_until else None,
                "reason": s.reason,
                "permanent": s.permanent,
            }
            for key, s in self._states.items()
        }

    def clear(self) -> None:
        with self._lock:
            self._states.clear()


_TABLE = LockoutTable()


def get_lockout_table() -> LockoutTable:
    return _TABLE
```

Wire-up: in `planner._stage2_elimination_reasons`, after the cooldown check, compute `model_id = provider.config.get("gateway", {}).get("model_id")` (via the existing `_first_config_or_field_string(provider, "gateway.model_id")` helper if it supports dotted keys; otherwise read `_config(provider).get("gateway", {}).get("model_id")`) and append `ProviderEliminated.MODEL_LOCKED_OUT` when `get_lockout_table().is_locked(LockoutKey(provider_id, model_id), now=now)`. In `production.execute_sync_prepared`'s `except Exception as exc` branch: if `isinstance(exc, QuotaExhausted)`, call `get_lockout_table().record_failure(key, now=completed_at, reason=exc.reason, reset_at=exc.reset_at)` and set `failure["code"] = exc.reason`; on the success path call `record_success`. Cooldown (provider-level) stays health-probe-driven and untouched.

- [x] **Step 4: Verify and commit**

Run: `uv run --frozen pytest tests/routing -q` → PASS.

```bash
git add src/pitwall/routing/lockout.py src/pitwall/routing/planner.py src/pitwall/routing/types.py src/pitwall/routing/production.py tests/routing/test_lockout.py tests/routing/test_production_routing.py
git commit -s -m "feat(routing): per-model lockout with quota-aware backoff and terminal bans"
```

---

### Task 7: Strict zero-cost filter (`routing/zero_cost.py`) and privacy surfacing

**Files:**
- Create: `src/pitwall/routing/zero_cost.py`
- Modify: `src/pitwall/routing/planner.py` (`_stage2_elimination_reasons`), `src/pitwall/routing/types.py` (`ProviderEliminated.ZERO_COST_UNVERIFIED`, `ScoreExplanation.trains_on_prompts: bool | None`), `src/pitwall/routing/scoring.py`
- Test: `tests/routing/test_zero_cost.py`

**Interfaces:**
- Produces: `zero_cost_verdict(provider) -> ZeroCostVerdict(allowed: bool, reason: str | None, trains_on_prompts: bool)`; the single implementation of ADR 0007 rule 2 (Task 1).

- [x] **Step 1: Write the failing evidence-matrix test**

```python
import pytest

from pitwall.routing.zero_cost import zero_cost_verdict


@pytest.mark.parametrize(
    ("catalog", "allowed", "reason"),
    [
        ({"free_type": "keyless", "tos": "caution"}, True, None),
        ({"free_type": "recurring-monthly", "tos": "ok", "hard_stop_guaranteed": True}, True, None),
        ({"free_type": "recurring-monthly", "tos": "ok"}, False, "hard-stop-not-guaranteed"),
        ({"free_type": "keyless", "tos": "avoid"}, False, "tos-avoid"),
        ({"free_type": "keyless", "tos": "unknown"}, False, "tos-unknown"),
        (
            {
                "free_type": "recurring-monthly",
                "tos": "ok",
                "hard_stop_guaranteed": True,
                "eligibility_gate": "regional-identity",
            },
            False,
            "eligibility-gated",
        ),
        ({"free_type": "discontinued", "tos": "ok"}, False, "discontinued"),
        ({}, False, "no-catalog-evidence"),
    ],
)
def test_zero_cost_evidence_matrix(catalog, allowed, reason) -> None:
    verdict = zero_cost_verdict(_provider(catalog=catalog))
    assert (verdict.allowed, verdict.reason) == (allowed, reason)


def test_metered_provider_is_not_subject_to_the_filter() -> None:
    assert zero_cost_verdict(_provider(mode="per_token", catalog={})).allowed is True


def test_trains_on_prompts_is_surfaced_in_plan_explanation() -> None:
    from pitwall.routing.scoring import explain_score

    explanation = explain_score(
        _provider(catalog={"free_type": "keyless", "tos": "ok", "trains_on_prompts": True})
    )
    assert explanation.trains_on_prompts is True
    assert explanation.to_dict()["trains_on_prompts"] is True
```

`_provider(mode="zero", catalog=...)` builds a gateway `Provider` whose `config["gateway"]["catalog"]` is the given dict (same shape as `tests/routing/test_quota_gate.py:_provider`).

- [x] **Step 2: Verify failure, then implement**

```python
"""Strict zero-cost filter: ADR 0007 rule 2 as one pure function (research §9.5)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pitwall.cost.estimator import parse_pricing_model


@dataclass(frozen=True, slots=True)
class ZeroCostVerdict:
    allowed: bool
    reason: str | None
    trains_on_prompts: bool


def _catalog(provider: Any) -> Mapping[str, Any]:
    config = getattr(provider, "config", None) or (
        provider.get("config") if isinstance(provider, Mapping) else {}
    )
    gateway = config.get("gateway", {}) if isinstance(config, Mapping) else {}
    catalog = gateway.get("catalog", {}) if isinstance(gateway, Mapping) else {}
    return catalog if isinstance(catalog, Mapping) else {}


def zero_cost_verdict(provider: Any) -> ZeroCostVerdict:
    catalog = _catalog(provider)
    trains = bool(catalog.get("trains_on_prompts", False))
    try:
        kind = parse_pricing_model(provider).kind
    except TypeError, ValueError:
        kind = "unknown"
    if kind != "zero":
        return ZeroCostVerdict(True, None, trains)
    if not catalog:
        return ZeroCostVerdict(False, "no-catalog-evidence", trains)
    tos = str(catalog.get("tos", "unknown"))
    if tos == "avoid":
        return ZeroCostVerdict(False, "tos-avoid", trains)
    if tos == "unknown":
        return ZeroCostVerdict(False, "tos-unknown", trains)
    if catalog.get("eligibility_gate"):
        return ZeroCostVerdict(False, "eligibility-gated", trains)
    free_type = str(catalog.get("free_type", ""))
    if free_type == "discontinued":
        return ZeroCostVerdict(False, "discontinued", trains)
    if free_type == "keyless" or bool(catalog.get("hard_stop_guaranteed", False)):
        return ZeroCostVerdict(True, None, trains)
    return ZeroCostVerdict(False, "hard-stop-not-guaranteed", trains)
```

Planner: append `ProviderEliminated.ZERO_COST_UNVERIFIED` with the verdict reason in Stage 2 when `not verdict.allowed`. Scoring: set `ScoreExplanation.trains_on_prompts = zero_cost_verdict(provider).trains_on_prompts`.

- [x] **Step 3: Verify and commit**

Run: `uv run --frozen pytest tests/routing/test_zero_cost.py tests/routing -q` → PASS.

```bash
git add src/pitwall/routing/zero_cost.py src/pitwall/routing/planner.py src/pitwall/routing/types.py src/pitwall/routing/scoring.py tests/routing/test_zero_cost.py
git commit -s -m "feat(routing): strict zero-cost evidence filter with trains_on_prompts surfaced"
```

---

### Task 8: Tier cascade, emergency descent, and the prong-3 escape hatch

> **Review correction (2026-09-10, low-level review of the finished branch):** the
> `emergency_descent` helper was unreachable. `attempts` can never be empty while any candidate is
> ranked, because zero-priced keyless candidates carry a zero ceiling and survive both the budget
> elimination and the reservation loop. The helper was removed; the behavior is pinned by
> `test_keyless_floor_survives_when_paid_rungs_are_budget_blocked` in the production tests.
> `escape_hatch_message` now accepts both bare and `stage:reason` elimination strings.

**Files:**
- Create: `src/pitwall/routing/cascade_seed.py`
- Modify: `src/pitwall/routing/production.py` (`_route_attempts` fallback assembly and `ProductionRoutePlan.to_dict` explanation), `tools/gateway/sync_catalog.py` (import `ladder_rank` from `cascade_seed` instead of its local `_ladder_rank`)
- Test: `tests/routing/test_cascade_seed.py`, `tests/routing/test_production_routing.py` (extend)

**Interfaces:**
- Produces: `ladder_rank(provider_like) -> int` (0 own-serve, 1 keyless, 2 budgeted free, 3 cheap metered, 4 other), `order_ladder(providers) -> tuple`, `emergency_descent(attempts, eliminated, *, now) -> tuple` (re-admits keyless-floor providers eliminated only for `budget`/`cooldown` reasons on paid rungs), `escape_hatch_message(plan, *, quota_snapshot, now, own_pod_usd_per_hour: Decimal, gpu_class: str) -> str | None` (the caller in `production.py` supplies the cheapest secure-cloud fit from `pitwall.models.fit_options` for the capability's `served_model_id`) returning the exact text `"all free pools exhausted until HH:MM UTC; own-pod at $X/hr would cover the gap — pitwall serve --model <m> --gpu-class <g>"` when every `zero`-priced candidate is `quota_ineligible` and no metered candidate remains.

- [x] **Step 1: Failing tests**

```python
def test_ladder_orders_own_serve_keyless_budgeted_metered() -> None:
    ranked = order_ladder([_metered(), _budgeted_free(), _keyless(), _own_serve()])
    assert [p.name for p in ranked] == ["own", "keyless", "budgeted", "metered"]


def test_emergency_descent_readmits_keyless_floor_when_paid_rungs_are_blocked() -> None:
    attempts = (_attempt(_metered(), eliminated="budget_blocked"),)
    eliminated = {_keyless().id: ["health_cooldown"]}
    descended = emergency_descent(attempts, eliminated, candidates=[_keyless()], now=NOW)
    assert [a.provider_id for a in descended][-1] == _keyless().id


def test_escape_hatch_message_when_all_free_exhausted() -> None:
    message = escape_hatch_message(
        _plan_all_quota_ineligible(reset_at=NOW + dt.timedelta(hours=2)),
        quota_snapshot=_snapshot(),
        now=NOW,
        own_pod_usd_per_hour=Decimal("0.50"),
        gpu_class="RTX 4090",
    )
    assert (
        message
        == "all free pools exhausted until 14:00 UTC; own-pod at $0.50/hr would cover the gap — pitwall serve --model coding.chat --gpu-class RTX 4090"
    )


def test_escape_hatch_is_never_auto_executed() -> None:
    plan = _plan_all_quota_ineligible(reset_at=NOW + dt.timedelta(hours=2))
    assert plan.to_dict()["escape_hatch"]["proposed_command"].startswith("pitwall serve")
    assert plan.to_dict()["escape_hatch"]["executed"] is False
```

- [x] **Step 2: Implement `cascade_seed.py`**

```python
"""Tier ladder, emergency descent, and the prong-3 escape hatch (research §9.6)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from pitwall.cost.estimator import parse_pricing_model
from pitwall.routing.quota import QuotaSnapshot, _pool_key, _provider_id

_PAID_BLOCKERS = frozenset({"budget_blocked", "health_cooldown", "quota_ineligible"})


def _catalog(provider: Any) -> Mapping[str, Any]:
    config = getattr(provider, "config", None) or (
        provider.get("config") if isinstance(provider, Mapping) else {}
    )
    gateway = config.get("gateway", {}) if isinstance(config, Mapping) else {}
    return gateway.get("catalog", {}) if isinstance(gateway, Mapping) else {}


def ladder_rank(provider: Any) -> int:
    """0 own-serve, 1 keyless, 2 budgeted free, 3 cheap metered, 4 everything else."""
    provider_type = str(
        getattr(provider, "provider_type", None)
        or (provider.get("provider_type") if isinstance(provider, Mapping) else "")
    )
    if provider_type == "pod_lease":
        return 0
    try:
        kind = parse_pricing_model(provider).kind
    except TypeError, ValueError:
        return 4
    catalog = _catalog(provider)
    if kind == "zero" and catalog.get("free_type") == "keyless":
        return 1
    if kind == "zero":
        return 2
    if kind == "per_token":
        return 3
    return 4


def order_ladder(providers: Sequence[Any]) -> tuple[Any, ...]:
    return tuple(sorted(providers, key=lambda p: (ladder_rank(p), _provider_id(p))))


def emergency_descent(
    attempts: Sequence[Any],
    eliminated: Mapping[str, Sequence[str]],
    *,
    candidates: Sequence[Any],
    now: dt.datetime,
) -> tuple[Any, ...]:
    """Re-admit keyless-floor providers whose only blockers were paid-rung failures."""
    if any(getattr(a, "eliminated", None) is None for a in attempts):
        return tuple(attempts)
    floor = [
        p
        for p in candidates
        if ladder_rank(p) == 1
        and set(eliminated.get(_provider_id(p), ())) <= _PAID_BLOCKERS - {"quota_ineligible"}
    ]
    return tuple(attempts) + tuple(_attempt_for(p, now=now) for p in order_ladder(floor))


def _attempt_for(provider: Any, *, now: dt.datetime) -> Any:
    from pitwall.routing.types import (
        RouteAttempt,
    )  # local import keeps this module pure for the extractor

    return RouteAttempt(
        provider_id=_provider_id(provider),
        provider=provider,
        attempt=0,
        backoff_s=0.0,
        reason="emergency_free_descent",
    )


def escape_hatch_message(
    plan: Any,
    *,
    quota_snapshot: QuotaSnapshot,
    now: dt.datetime,
    own_pod_usd_per_hour: Decimal,
    gpu_class: str,
) -> str | None:
    zero_candidates = [c for c in plan.ranked_candidates if ladder_rank(c.provider) in (1, 2)]
    metered = [c for c in plan.ranked_candidates if ladder_rank(c.provider) == 3]
    if not zero_candidates or metered:
        return None
    reasons = plan.dropped_provider_reasons
    if not all("quota_ineligible" in reasons.get(c.provider_id, []) for c in zero_candidates):
        return None
    resets = [
        r.reset_at
        for c in zero_candidates
        if (r := quota_snapshot.get(c.provider_id, _pool_key(c.provider))) is not None
        and r.reset_at is not None
    ]
    until = min(resets).astimezone(dt.UTC).strftime("%H:%M") if resets else "unknown"
    model = plan.capability_snapshot.served_model_id or plan.capability_snapshot.name
    return (
        f"all free pools exhausted until {until} UTC; own-pod at ${own_pod_usd_per_hour:.2f}/hr would cover the gap "
        f"— pitwall serve --model {model} --gpu-class {gpu_class}"
    )
```

Notes: the module stays pure (no I/O); `production.py` resolves `own_pod_usd_per_hour` and `gpu_class` from `pitwall.models.fit_options` before calling it; the reset time is the minimum `reset_at` across the ineligible zero-priced candidates. Test helpers `_metered/_budgeted_free/_keyless/_own_serve/_attempt/_plan_all_quota_ineligible/_snapshot` live in `tests/routing/test_cascade_seed.py` and build `Provider` records the same way `tests/routing/test_stage3_scoring.py:16-35` does. `production.py` calls `emergency_descent` after `_route_attempts` when the primary chain contains no admissible attempt, and adds `"escape_hatch": {"proposed_command": …, "executed": False, "reason": …}` to `ProductionRoutePlan.to_dict()` when `escape_hatch_message` returns text. The extractor's chain order (Task 3) now comes from `order_ladder`.

- [x] **Step 3: Verify and commit**

Run: `uv run --frozen pytest tests/routing tests/tools/test_gateway_sync_catalog.py -q` → PASS.

```bash
git add src/pitwall/routing/cascade_seed.py src/pitwall/routing/production.py tools/gateway/sync_catalog.py tests/routing/test_cascade_seed.py tests/routing/test_production_routing.py
git commit -s -m "feat(routing): tier ladder, emergency free descent, and prong-3 escape hatch proposal"
```

---

### Task 9: Reconciler quota poll job

**Files:**
- Modify: `src/pitwall/reconciler/__init__.py` (new `_quota_poll(ctx)` after `_lease_expiry_reconcile`; register `cron(_quota_poll, minute=set(range(0, 60, 5)))` in the `cron_jobs` list at `:1808`)
- Test: `tests/reconciler/test_quota_poll.py`

**Interfaces:**
- Consumes: `QuotaRepository`, `GatewayProvider.availability` (catalog evidence), `get_lockout_table().snapshot()`.
- Produces: `_quota_poll(ctx)` which, for every enabled `openai_gateway` provider: seeds a `QuotaRecord` from `config.gateway.catalog` when none exists (`budget_units = monthly_tokens or credit_tokens or None`, `window_start = first of month`, `reset_at = first of next month` for monthly types, `+1 day` for daily, `None` for keyless/uncapped), rolls the window when `reset_at <= now` (zero `used_units`, advance both stamps), writes a `provider_quota_samples` row, and stores `{"lockout": table.snapshot().get(key)}` in `evidence`. It never resurrects a lockout and never calls upstream.

- [x] **Step 1: Failing tests** (mock-pool pattern from `tests/reconciler/test_lease_expiry_reconcile.py:29-50`):

```python
async def test_quota_poll_seeds_monthly_window_from_catalog(monkeypatch) -> None:
    repo = AsyncMock()
    repo.list_all.return_value = ()
    monkeypatch.setattr("pitwall.reconciler.QuotaRepository", lambda _pool: repo)
    monkeypatch.setattr(
        "pitwall.reconciler.fetch_gateway_providers",
        AsyncMock(return_value=[_gateway_row(monthly_tokens=5_000_000)]),
    )
    await _quota_poll({"db_pool": _make_mock_pool(), "now": NOW})
    record = repo.upsert.await_args.args[0]
    assert (record.budget_units, record.used_units, record.reset_at) == (
        Decimal(5_000_000),
        Decimal(0),
        dt.datetime(2026, 10, 1, tzinfo=dt.UTC),
    )


async def test_quota_poll_rolls_expired_window_and_keeps_lockout_evidence_readonly(
    monkeypatch,
) -> None:
    existing = _record(
        used_units=Decimal(4_000_000),
        reset_at=NOW - dt.timedelta(minutes=1),
        window_start=NOW - dt.timedelta(days=31),
    )
    repo = AsyncMock()
    repo.list_all.return_value = (existing,)
    monkeypatch.setattr("pitwall.reconciler.QuotaRepository", lambda _pool: repo)
    monkeypatch.setattr(
        "pitwall.reconciler.fetch_gateway_providers", AsyncMock(return_value=[_gateway_row()])
    )
    await _quota_poll({"db_pool": _make_mock_pool(), "now": NOW})
    rolled = repo.upsert.await_args.args[0]
    assert rolled.used_units == Decimal(0) and rolled.reset_at == dt.datetime(
        2026, 10, 1, tzinfo=dt.UTC
    )
    assert "lockout" in rolled.evidence
    repo.record_sample.assert_awaited_once()
```

- [x] **Step 2: Implement `_quota_poll` and `fetch_gateway_providers(pool)`**

```python
_GATEWAY_PROVIDERS_SQL = """
    SELECT id, config FROM pitwall.providers
    WHERE enabled = true AND provider_type = 'openai_gateway'
    ORDER BY id
"""


async def fetch_gateway_providers(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(_GATEWAY_PROVIDERS_SQL)
    return [dict(row) for row in rows]


def _quota_window(
    free_type: str, now: dt.datetime
) -> tuple[dt.datetime | None, dt.datetime | None]:
    if free_type in ("recurring-monthly", "recurring-credit"):
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return start, (start + dt.timedelta(days=32)).replace(day=1)
    if free_type == "recurring-daily":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + dt.timedelta(days=1)
    return None, None


async def _quota_poll(ctx: dict[str, Any]) -> None:
    """Seed, roll, and sample free-pool quota windows every 5 minutes; never calls upstream."""
    pool: asyncpg.Pool | None = ctx.get("db_pool")
    if pool is None:
        return
    now = ctx["now"]() if callable(ctx.get("now")) else ctx.get("now") or dt.datetime.now(dt.UTC)
    repo = QuotaRepository(pool)
    existing = {(r.provider_id, r.pool_key): r for r in await repo.list_all()}
    lockouts = get_lockout_table().snapshot()
    for prov in await fetch_gateway_providers(pool):
        catalog = ((prov.get("config") or {}).get("gateway") or {}).get("catalog") or {}
        free_type = str(catalog.get("free_type", "recurring-uncapped"))
        pool_key = str(catalog.get("pool_key") or "")
        model_id = str(((prov.get("config") or {}).get("gateway") or {}).get("model_id", ""))
        record = existing.get((prov["id"], pool_key))
        if record is None:
            window_start, reset_at = _quota_window(free_type, now)
            budget = catalog.get("monthly_tokens") or catalog.get("credit_tokens") or None
            record = QuotaRecord(
                provider_id=prov["id"],
                pool_key=pool_key,
                free_type=free_type,
                window_start=window_start,
                reset_at=reset_at,
                budget_units=Decimal(str(budget)) if budget else None,
                used_units=Decimal(0),
                tos_verdict=str(catalog.get("tos", "unknown")),
                evidence={},
            )
        elif record.reset_at is not None and record.reset_at <= now:
            window_start, reset_at = _quota_window(free_type, now)
            record = replace(
                record, window_start=window_start, reset_at=reset_at, used_units=Decimal(0)
            )
        evidence = dict(record.evidence)
        evidence["lockout"] = lockouts.get(f"{prov['id']}/{model_id}")
        record = replace(record, evidence=evidence)
        try:
            await repo.upsert(record)
            await repo.record_sample(prov["id"], now, record.used_units, record.reset_at)
        except Exception as exc:  # reason: one provider's quota write must not stop the tick
            log.warning("quota poll failed for %s: %s", prov["id"], redact_text(exc))
```

Register `cron(_quota_poll, minute=set(range(0, 60, 5)))` in the `cron_jobs` list and add `quota_poll: every 5 minutes` to the docstring block at `:1795-1806`. Imports: `from dataclasses import replace`, `from pitwall.db.quota_repository import QuotaRepository`, `from pitwall.routing.lockout import get_lockout_table`, `from pitwall.routing.quota import QuotaRecord`.

- [x] **Step 3: Verify and commit**

Run: `uv run --frozen pytest tests/reconciler -q` → PASS.

```bash
git add src/pitwall/reconciler/__init__.py tests/reconciler/test_quota_poll.py
git commit -s -m "feat(reconciler): quota poll job seeds, rolls, and samples free-pool windows"
```

---

### Task 10: REST surfaces — `/v1/quotas`, `/v1/gateway/models`, admin refresh, proxy model-id map, scopes, OpenAPI baseline

**Files:**
- Create: `src/pitwall/api/routes/quotas.py`, `src/pitwall/api/routes/gateway_models.py`, `src/pitwall/api/schemas/quotas.py`
- Modify: `src/pitwall/api/app.py:134-156` (`_required_scope`) and `:524-543` (`include_router`), `src/pitwall/api/routes/openai.py:349-366` (`_resolve_provider_chain` model-id lookup), `docs/api/openapi-baseline.json`
- Test: `tests/api/test_quota_routes.py`, `tests/api/test_gateway_models_route.py`, `tests/api/test_openai_proxy_model_id_map.py`

**Interfaces:**
- Consumes: `QuotaRepository` (Task 5), `ModelIdMapping`.
- Produces: `GET /v1/quotas` (READ scope) → `{"quotas": [QuotaRecord.to_dict() + {"headroom": float, "lockout": {...} | null}]}`; `POST /v1/admin/quotas/refresh` (SERVER_ADMIN scope, runs `_quota_poll` once, returns `{"refreshed": n}`); `GET /v1/gateway/models` (READ) → `{"object": "list", "data": [{"id": model_id, "capability": …, "provider": …, "trains_on_prompts": bool, "tos": str}]}`; proxy: a request to `/v1/openai/{capability}/v1/chat/completions` whose body `model` starts with `gw/` is pinned to the mapped provider (`model_id_map`) and the body `model` is rewritten to the provider's `gateway.model_id` before egress; unmapped `gw/*` ids return the existing `provider_not_found` envelope.

- [x] **Step 1: Failing tests** (use `clear_app_module` + `httpx.ASGITransport` as in `tests/conftest.py:1080-1105`; stub `QuotaRepository` on `app.state` the way `tests/api/test_cost_read_routes.py` stubs its read model):

```python
async def test_quotas_read_requires_read_scope_and_lists_headroom(app_mod, read_token) -> None:
    app_mod.app.state.quota_repository = _FakeQuotaRepo([_record()])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app), base_url="http://test"
    ) as client:
        response = await client.get("/v1/quotas", headers={"Authorization": f"Bearer {read_token}"})
    assert response.status_code == 200
    [row] = response.json()["quotas"]
    assert row["headroom"] == pytest.approx(0.8) and row["tos_verdict"] == "ok"


async def test_admin_refresh_needs_server_admin_scope(app_mod, read_token, admin_token) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app), base_url="http://test"
    ) as client:
        denied = await client.post(
            "/v1/admin/quotas/refresh", headers={"Authorization": f"Bearer {read_token}"}
        )
        allowed = await client.post(
            "/v1/admin/quotas/refresh", headers={"Authorization": f"Bearer {admin_token}"}
        )
    assert denied.status_code == 403 and allowed.status_code == 200


async def test_gateway_models_lists_mapped_ids_with_privacy_flags(app_mod, read_token) -> None:
    app_mod.app.state.quota_repository = _FakeQuotaRepo(
        [], model_ids=[ModelIdMapping("gw/glm-flash", "coding.chat", "prov_gw")]
    )
    app_mod.app.state.provider_repository = _FakeProviderRepo(
        {
            "prov_gw": _provider(
                catalog={"free_type": "keyless", "tos": "caution", "trains_on_prompts": True}
            )
        }
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/v1/gateway/models", headers={"Authorization": f"Bearer {read_token}"}
        )
    assert response.status_code == 200
    assert response.json() == {
        "object": "list",
        "data": [
            {
                "id": "gw/glm-flash",
                "capability": "coding.chat",
                "provider": "prov_gw",
                "trains_on_prompts": True,
                "tos": "caution",
            }
        ],
    }


async def test_gw_model_id_pins_provider_and_rewrites_model(
    openai_proxy_chat_post, app_mod
) -> None:
    app_mod.app.state.quota_repository = _FakeQuotaRepo(
        [], model_ids=[ModelIdMapping("gw/glm-flash", "coding.chat", "prov_gw")]
    )
    response = await openai_proxy_chat_post(
        app_mod,
        "/v1/openai/coding.chat/v1/chat/completions",
        {"model": "gw/glm-flash", "messages": []},
    )
    assert response.headers["X-Pitwall-Provider-ID"] == "prov_gw"
    assert _captured_upstream_body()["model"] == "glm-4.7-flash"
```

Helpers for these tests: `_FakeQuotaRepo(records, model_ids=())` exposes `list_all/list_model_ids/upsert/upsert_model_id` as `AsyncMock`s; `_FakeProviderRepo(mapping)` exposes `get`; `read_token`/`admin_token` come from the scoped-token fixtures in `tests/api/test_admin_auth_matrix.py`; `_captured_upstream_body()` reads the `httpx.MockTransport` recorder installed by `openai_proxy_chat_post`.

- [x] **Step 2: Implement** — `quotas.py` mirrors `routes/cost.py` (pool from `request.app.state`, `QuotaRepository` injected via `getattr(request.app.state, "quota_repository", None) or QuotaRepository(pool)`); `_required_scope` gains `if path == "/v1/admin/quotas/refresh": return SERVER_ADMIN_SCOPE` (the `/v1/admin/` prefix rule already covers it; add an explicit test); `openai.py` `_resolve_provider_chain` accepts `model_id: str | None` and, when it starts with `gw/`, resolves through `QuotaRepository.list_model_ids()` to a single-provider chain. Regenerate the baseline: `uv run python tools/ci/export_openapi.py --output docs/api/openapi-baseline.json` and confirm `make openapi-check` reports only additions.

- [x] **Step 3: Verify and commit**

Run: `uv run --frozen pytest tests/api -q -p no:randomly 2>&1 | tail -2` → passed; `make openapi-check` → exit 0.

```bash
git add src/pitwall/api/routes/quotas.py src/pitwall/api/routes/gateway_models.py src/pitwall/api/schemas/quotas.py src/pitwall/api/app.py src/pitwall/api/routes/openai.py docs/api/openapi-baseline.json tests/api/test_quota_routes.py tests/api/test_gateway_models_route.py tests/api/test_openai_proxy_model_id_map.py
git commit -s -m "feat(api): quota reads, gateway model list, admin refresh, and gw/ model-id proxy pinning"
```

---

### Task 11: Claude-native `/v1/messages` surface (Decision Q1)

**Files:**
- Create: `src/pitwall/api/anthropic_translate.py`, `src/pitwall/api/routes/messages.py`, `src/pitwall/api/schemas/messages.py`
- Modify: `src/pitwall/api/app.py` (`include_router(messages_router)`, scope rule `path == "/v1/messages" → SPEND_SCOPE`), `docs/api/openapi-baseline.json`
- Test: `tests/api/test_messages_translate.py` (pure), `tests/api/test_messages_route.py` (ASGI + fake upstream), `tests/api/test_messages_sse.py`

**Interfaces:**
- Produces: `anthropic_to_openai(body: Mapping) -> dict` (system → leading system message; content blocks → text; `tools` → OpenAI `tools`; `max_tokens` required; `stop_sequences` → `stop`), `openai_to_anthropic(body: Mapping, *, request_model: str) -> dict` (`{"id","type":"message","role":"assistant","model","content":[{"type":"text","text"}],"stop_reason": map(finish_reason),"usage":{"input_tokens","output_tokens"}}`), `sse_openai_to_anthropic(chunks: AsyncIterator[bytes]) -> AsyncIterator[bytes]` emitting `message_start`, `content_block_start`, `content_block_delta`, `content_block_stop`, `message_delta`, `message_stop` events. Route: `POST /v1/messages` with header `anthropic-version` echoed; `model` maps through `model_id_map` (`gw/*`) or is treated as a capability name; executes through `ProductionRoutingService.execute_sync` (non-stream) or the openai proxy relay (`stream: true`). Errors use Anthropic's envelope `{"type":"error","error":{"type","message"}}` with `invalid_request_error` (400), `authentication_error` (401), `permission_error` (403), `rate_limit_error` (429 from `QuotaExhausted`), `overloaded_error` (503), `api_error` (500).

- [x] **Step 1: Failing pure translation tests**

```python
def test_system_and_blocks_become_openai_messages() -> None:
    out = anthropic_to_openai(
        {
            "model": "gw/m",
            "max_tokens": 64,
            "system": "be terse",
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            "stop_sequences": ["END"],
            "temperature": 0.2,
        }
    )
    assert out["messages"][0] == {"role": "system", "content": "be terse"}
    assert out["messages"][1] == {"role": "user", "content": "hi"}
    assert out["max_tokens"] == 64 and out["stop"] == ["END"] and out["temperature"] == 0.2


def test_missing_max_tokens_is_invalid_request() -> None:
    with pytest.raises(AnthropicInvalidRequest, match="max_tokens"):
        anthropic_to_openai({"model": "gw/m", "messages": []})


def test_openai_response_maps_finish_reason_and_usage() -> None:
    out = openai_to_anthropic(
        {
            "id": "x",
            "choices": [{"message": {"content": "ok"}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1},
        },
        request_model="gw/m",
    )
    assert out["stop_reason"] == "max_tokens" and out["usage"] == {
        "input_tokens": 3,
        "output_tokens": 1,
    }
    assert out["content"] == [{"type": "text", "text": "ok"}]


async def test_sse_relay_emits_anthropic_event_sequence() -> None:
    chunks = [
        b'data: {"choices":[{"delta":{"content":"he"}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":"y"},"finish_reason":"stop"}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    events = [e async for e in sse_openai_to_anthropic(_aiter(chunks), request_model="gw/m")]
    names = [line.split(b"event: ")[1].split(b"\n")[0] for line in events]
    assert names == [
        b"message_start",
        b"content_block_start",
        b"content_block_delta",
        b"content_block_delta",
        b"content_block_stop",
        b"message_delta",
        b"message_stop",
    ]
```

- [x] **Step 2: Failing route tests** — non-stream through a stubbed `ProductionRoutingService` (pattern: `tests/api/test_inference_contract.py`), stream through the proxy relay with an `httpx.MockTransport` upstream (pattern: `tests/api/test_openai_proxy.py`), plus scope (SPEND) and the error envelope for `QuotaExhausted` → 429 `rate_limit_error`.

- [x] **Step 3: Implement** the translator (pure, no I/O) and the route: build the OpenAI body, then either `await service.execute_sync(capability_id=…, payload=body, provider_id=pinned)` and return `openai_to_anthropic(execution.output, ...)`, or for `stream: true` reuse `_relay_upstream_bytes` from `routes/openai.py` wrapped by `sse_openai_to_anthropic`. Register the router and scope; regenerate the OpenAPI baseline.

- [x] **Step 4: Verify and commit**

Run: `uv run --frozen pytest tests/api/test_messages_translate.py tests/api/test_messages_route.py tests/api/test_messages_sse.py tests/api/test_openapi_compat_policy.py -q` → PASS; `make openapi-check` → 0.

```bash
git add src/pitwall/api/anthropic_translate.py src/pitwall/api/routes/messages.py src/pitwall/api/schemas/messages.py src/pitwall/api/app.py docs/api/openapi-baseline.json tests/api/test_messages_translate.py tests/api/test_messages_route.py tests/api/test_messages_sse.py
git commit -s -m "feat(api): Claude-native /v1/messages surface translating to the production route"
```

---

### Task 12: CLI `gateway` and `quotas` nouns; personal `serve --gateway` child-process supervision (Decisions Q2, Q4)

**Files:**
- Create: `src/pitwall/cli_gateway.py`, `src/pitwall/personal/gateway.py`
- Modify: `src/pitwall/cli.py:159-235` (dispatch table: `gateway`, `quotas`), `src/pitwall/cli_personal.py:93-102` (`serve` gains `--gateway`), `src/pitwall/personal/service.py` (call the supervisor), `src/pitwall/cli.py` `_usage`
- Test: `tests/cli/test_gateway_cli.py`, `tests/personal/test_gateway_supervisor.py`

**Interfaces:**
- Produces: `pitwall gateway sync --version V [--from-json PATH]` (delegates to `tools.gateway.sync_catalog.main`), `pitwall gateway status [--json]` (reads `config/gateway-catalog.lock.json` + `GET /v1/quotas` when `PITWALL_API_URL` is set, else local lock only), `pitwall gateway doctor [--json]` (checks: catalog lock present, seeds hash matches lock, gateway process healthy on `http://127.0.0.1:20130/health` with bearer, exactly one front door — i.e. `PITWALL_GATEWAY_URL` is loopback), `pitwall quotas [--json]` (table: provider, pool, free_type, used/budget, headroom bar, reset countdown, tos badge, lockout). `personal.gateway.GatewaySupervisor(state: StateStore, argv: list[str], health_url: str)` with `start() -> GatewayProcess(pid, started_at, health_url)`, `stop()`, `health() -> bool`, persisted as `gateway.json` under `StateStore` (owner-only, atomic, same directory as personal leases). `serve --gateway` starts the supervisor before launching and `stop --all` stops it.

- [x] **Step 1: Failing tests** — CLI tests drive `pitwall.cli.main([...])` with `capsys` (pattern: `tests/cli/test_init_seed.py`); supervisor tests inject a fake `subprocess.Popen` and an `httpx.MockTransport` health endpoint and assert the state file is `0600`, `start()` is idempotent when the recorded pid is alive, and `stop()` tolerates an already-exited process.

- [x] **Step 2: Implement** `personal/gateway.py`:

```python
"""Child-process supervision for the loopback gateway in personal mode (Decision Q2)."""

from __future__ import annotations

import datetime as dt
import os
import signal
import subprocess
from collections.abc import Mapping
from typing import Callable

import httpx
from pydantic import BaseModel

from pitwall.personal.state import StateStore

GATEWAY_STATE = "gateway.json"
DEFAULT_HEALTH_URL = "http://127.0.0.1:20130/health"


class GatewayProcess(BaseModel):
    pid: int
    started_at: dt.datetime
    health_url: str
    argv: list[str]


class GatewaySupervisor:
    def __init__(
        self,
        store: StateStore,
        *,
        argv: list[str],
        health_url: str = DEFAULT_HEALTH_URL,
        env: Mapping[str, str] | None = None,
        popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._store, self._argv, self._health_url = store, list(argv), health_url
        self._env = dict(os.environ if env is None else env)
        self._popen, self._transport = popen, transport

    def current(self) -> GatewayProcess | None:
        raw = self._store.read_document(GATEWAY_STATE)
        return GatewayProcess.model_validate(raw) if raw else None

    def start(self) -> GatewayProcess:
        token = self._env.get("PITWALL_GATEWAY_TOKEN")
        if not token:
            raise RuntimeError(
                "PITWALL_GATEWAY_TOKEN must be set; the gateway refuses anonymous mode"
            )
        existing = self.current()
        if existing is not None and _alive(existing.pid):
            return existing
        child_env = {**self._env, "PITWALL_GATEWAY_TOKEN": token}  # env only, never argv
        process = self._popen(
            self._argv, env=child_env, stdin=subprocess.DEVNULL, start_new_session=True
        )
        record = GatewayProcess(
            pid=process.pid,
            started_at=dt.datetime.now(dt.UTC),
            health_url=self._health_url,
            argv=self._argv,
        )
        self._store.write_document(
            GATEWAY_STATE, record.model_dump(mode="json")
        )  # 0600 atomic, same as leases
        return record

    def stop(self) -> None:
        existing = self.current()
        if existing is not None and _alive(existing.pid):
            os.killpg(os.getpgid(existing.pid), signal.SIGTERM)
        self._store.remove_document(GATEWAY_STATE)

    def health(self) -> bool:
        token = self._env.get("PITWALL_GATEWAY_TOKEN", "")
        try:
            with httpx.Client(transport=self._transport, timeout=2.0) as client:
                return (
                    client.get(
                        self._health_url, headers={"Authorization": f"Bearer {token}"}
                    ).status_code
                    == 200
                )
        except httpx.HTTPError:
            return False


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
```

`StateStore.read_document/write_document/remove_document` are the generic owner-only atomic helpers the lease store already uses internally (`src/pitwall/personal/state.py:49-80`); expose them by those names if they are private today. The supervisor launches `node packages/gateway/dist/shim.js --port 20130 --bind 127.0.0.1` (Task 18 provides the binary; until then the supervisor argv defaults to `["omniroute", "serve", "--port", "20130"]` for the stock sidecar) with `PITWALL_GATEWAY_TOKEN` passed through the environment, never argv. `pitwall doctor` (existing `cmd_config check`-style command; add a `gateway` section) verifies the single front door.

- [x] **Step 3: Verify and commit**

Run: `uv run --frozen pytest tests/cli/test_gateway_cli.py tests/personal -q` → PASS.

```bash
git add src/pitwall/cli_gateway.py src/pitwall/personal/gateway.py src/pitwall/cli.py src/pitwall/cli_personal.py src/pitwall/personal/service.py tests/cli/test_gateway_cli.py tests/personal/test_gateway_supervisor.py
git commit -s -m "feat(cli): gateway sync/status/doctor, quotas view, and personal-mode gateway supervision"
```

---

### Task 13: TUI — quota columns on Providers, free-tier burn-down panel on Cost

**Files:**
- Modify: `src/pitwall/tui/providers.py:61-108` (`ProviderEntry` gains `quota_headroom: float | None`, `reset_in: str | None`, `tos: str | None`; `as_row` grows to 12 cells; `format_providers_table` widths to 12), `src/pitwall/tui/cost.py:47-95` (`CostSnapshot.free_burn_down: tuple[FreeBurnDownRow, ...]`), `src/pitwall/tui/cost.py:180-250` (`CostScreen.compose` adds `Static(id="cost-free-burn-down")`), sources `PostgresProvidersSource` and `PostgresCostSource` read `QuotaRepository`
- Test: `tests/tui/test_providers_quota_columns.py`, `tests/tui/test_cost_free_burn_down.py` (Pilot-driven like the existing `tests/tui/` screens)

- [x] **Step 1: Failing tests** assert `format_providers_table` renders `HEADROOM  RESET  TOS` columns with `[####------] 40%`, `2d 3h`, and `caution`, and that the Cost screen shows `free-tier burn-down` with one line per pool `alpha-pool  1.0M/5.0M  resets 2026-10-01`.

- [x] **Step 2: Implement**; keep `StaticProvidersSource` and `StaticCostSource` hermetic fixtures updated so the Pilot tests need no DB.

- [x] **Step 3: Verify and commit**

Run: `uv run --frozen pytest tests/tui -q` → PASS.

```bash
git add src/pitwall/tui/providers.py src/pitwall/tui/cost.py tests/tui/test_providers_quota_columns.py tests/tui/test_cost_free_burn_down.py
git commit -s -m "feat(tui): quota headroom, reset countdown, tos badge, and free-tier burn-down"
```

---

### Task 14: MCP — `pitwall_gateway_catalog_read` and `pitwall_quota_list` (76 → 78)

**Files:**
- Create: `src/pitwall/mcp/tools/gateway.py`
- Modify: `src/pitwall/mcp/registry.py:3,114,336` (docstring and both asserts to 78; two literal `ToolSpec` entries with `scope="gateway"`), `tests/mcp/test_registry.py:11-13`, `tests/mcp/test_registry_health.py`, `docs/sdlc/03-mcp-server.md` (intro, invariant, new section), `docs/support-matrix.md:11`, `README.md:58`, `docs/operator/user-journey-catalog.md` (J10 row)
- Test: `tests/mcp/test_gateway_tools.py`; `tests/mcp/test_doc_count_sync.py` (existing, must pass unchanged)

- [x] **Step 1: Failing tests**

```python
async def test_gateway_catalog_read_is_read_only_and_hermetic(monkeypatch) -> None:
    from pitwall.mcp.tools.gateway import pitwall_gateway_catalog_read

    result = await pitwall_gateway_catalog_read(tos="ok")
    assert all(row["tos"] == "ok" for row in result["providers"])
    assert result["source"] == "config/gateway-catalog.json"


async def test_quota_list_uses_quota_repository(monkeypatch) -> None:
    monkeypatch.setattr("pitwall.mcp.tools.gateway.get_pool", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        "pitwall.mcp.tools.gateway.QuotaRepository", lambda _pool: _FakeQuotaRepo([_record()])
    )
    from pitwall.mcp.tools.gateway import pitwall_quota_list

    assert (await pitwall_quota_list())["quotas"][0]["headroom"] == pytest.approx(0.8)


def test_registry_now_has_78_tools() -> None:
    assert (
        len(TOOL_NAMES) == 78
        and {"pitwall_gateway_catalog_read", "pitwall_quota_list"} <= TOOL_NAMES
    )
```

- [x] **Step 2: Implement**, update every count site listed above in the same commit, and run `uv run --frozen pytest tests/mcp -q -p no:randomly` → PASS including `test_doc_count_sync.py` and `test_no_business_logic_guard.py` (the tool reads `config/gateway-catalog.json` through `pitwall.routing.zero_cost` and `QuotaRepository`, never `pitwall.cost` directly).

- [x] **Step 3: Commit**

```bash
git add src/pitwall/mcp/tools/gateway.py src/pitwall/mcp/registry.py tests/mcp/test_gateway_tools.py tests/mcp/test_registry.py tests/mcp/test_registry_health.py docs/sdlc/03-mcp-server.md docs/support-matrix.md README.md docs/operator/user-journey-catalog.md
git commit -s -m "feat(mcp): gateway catalog read and quota list tools (78 tools)"
```

---

### Task 15: Exporter free-pool metrics

**Files:**
- Modify: `src/pitwall/cost/exporter.py:44-90` (gauges) and `:95-216` (`_refresh`)
- Test: `tests/cost/test_exporter_free_pools.py`

**Interfaces:**
- Produces gauges `pitwall_free_pool_headroom{provider,pool}` (0..1), `pitwall_free_pool_reset_seconds{provider,pool}`, `pitwall_free_pool_locked{provider,model}` (0/1), `pitwall_free_tokens_used_month{pool}`, and counter `pitwall_free_pool_429_total{provider,model,reason}` incremented from `LockoutTable.record_failure`.

- [x] **Step 1: Failing test** renders `/metrics` after seeding the fake pool with one `provider_quotas` row and one lockout and asserts the four series plus `# HELP` lines exist.
- [x] **Step 2: Implement**

```python
free_pool_headroom = Gauge(
    "pitwall_free_pool_headroom",
    "Remaining share of a free pool's window (0..1)",
    ["provider", "pool"],
)
free_pool_reset_seconds = Gauge(
    "pitwall_free_pool_reset_seconds",
    "Seconds until a free pool's window resets",
    ["provider", "pool"],
)
free_pool_locked = Gauge(
    "pitwall_free_pool_locked",
    "1 when a (provider, model) tuple is locked out",
    ["provider", "model"],
)
free_tokens_used_month = Gauge(
    "pitwall_free_tokens_used_month", "Free tokens consumed this window", ["pool"]
)
free_pool_429_total = Counter(
    "pitwall_free_pool_429_total",
    "Quota/rate-limit signals by reason",
    ["provider", "model", "reason"],
)
```

In `_refresh`, after the provider-spend block:

```python
        quota_rows = await conn.fetch(
            "SELECT provider_id, pool_key, budget_units, used_units, reset_at FROM pitwall.provider_quotas"
        )
    for row in quota_rows:
        budget = Decimal(row["budget_units"]) if row["budget_units"] else None
        used = Decimal(row["used_units"])
        headroom = float((budget - used) / budget) if budget else 1.0
        free_pool_headroom.labels(provider=row["provider_id"], pool=row["pool_key"]).set(max(0.0, min(1.0, headroom)))
        seconds = (row["reset_at"] - datetime.now(UTC)).total_seconds() if row["reset_at"] else 0.0
        free_pool_reset_seconds.labels(provider=row["provider_id"], pool=row["pool_key"]).set(max(0.0, seconds))
        free_tokens_used_month.labels(pool=row["pool_key"] or row["provider_id"]).set(float(used))
    for key, state in get_lockout_table().snapshot().items():
        provider, _, model = key.partition("/")
        free_pool_locked.labels(provider=provider, model=model).set(1 if state["locked_until"] or state["permanent"] else 0)
```

`lockout.py` gains `LockoutTable.__init__(..., on_failure: Callable[[LockoutKey, str], None] | None = None)`; the exporter registers `lambda key, reason: free_pool_429_total.labels(provider=key.provider_id, model=key.model_id, reason=reason).inc()` at startup so the routing package never imports prometheus.
- [x] **Step 3: Verify and commit**

Run: `uv run --frozen pytest tests/cost/test_exporter_free_pools.py tests/cost -q` → PASS.

```bash
git add src/pitwall/cost/exporter.py src/pitwall/routing/lockout.py tests/cost/test_exporter_free_pools.py
git commit -s -m "feat(exporter): free-pool headroom, reset, lockout, and 429 metrics"
```

---

### Task 16: Agent Routing `gateway` seat and route attachment

**Files:**
- Modify: `packages/agent-routing/runtime/model_routing/routes.py:47` (`SEATS += ("gateway",)`), `packages/agent-routing/docs/routes.md:71,122`, `packages/agent-routing/plugins/pitwall/skills/subagent-model-routing/SKILL.md` (seat table), `src/pitwall/personal/routes.py:30-45` (`attach(..., seat="local")` parameter; gateway routes attach with `--seat gateway`)
- Test: `packages/agent-routing/tests/test_routes.py` (extend), `tests/personal/test_routes_gateway_seat.py`

- [x] **Step 1: Failing tests** — `routes add gw --model gw/glm-flash --base-url http://127.0.0.1:8000/v1/openai/coding.chat/v1 --api-key-env PITWALL_API_TOKEN --seat gateway` is accepted and listed with seat `gateway`; the personal `RouteRunner.attach(..., seat="gateway")` argv ends with `--seat gateway`.
- [x] **Step 2: Implement** and run `cd packages/agent-routing && python3 -m unittest tests.test_routes tests.test_plugin_identity && .venv/bin/ruff check runtime tests && .venv/bin/mypy --python-version 3.14 runtime/model_routing` → OK / clean.
- [x] **Step 3: Commit**

```bash
git add packages/agent-routing/runtime/model_routing/routes.py packages/agent-routing/docs/routes.md packages/agent-routing/plugins/pitwall/skills/subagent-model-routing/SKILL.md packages/agent-routing/tests/test_routes.py src/pitwall/personal/routes.py tests/personal/test_routes_gateway_seat.py
git commit -s -m "feat(agent-routing): gateway seat for Pitwall free-tier routes"
```

---

### Task 17: Phase 0 benchmark tool, TPM soak test, and dossier

**Files:**
- Create: `tools/gateway/bench_free_pools.py`, `tests/tools/test_bench_free_pools.py`, `tests/slow/test_gateway_tpm_soak.py` (marked `slow`), `docs/research/2026-09-10-free-pool-benchmark.md`

**Interfaces:**
- Produces: `bench_free_pools.run(pools: Sequence[BenchPool], *, minutes: int, rpm: int, transport=None) -> BenchReport(rows: tuple[BenchRow(provider, model, requests, ok, http_429, p50_ms, p95_ms, cost_usd: Decimal, verdict: Literal["keep","kill"])])` with `verdict = "keep"` iff `ok/requests >= 0.95 and p95_ms <= 8000 and cost_usd == 0`; `render_markdown(report) -> str`. The soak test drives `ProductionRoutingService` against a fake OpenAI server that returns 429 after N requests per pool and asserts `MONTH_TO_DATE_SPEND_SQL` stays `0` and the 429 count per pool is `<= 1 + minutes` (one probe per lockout window).

- [x] **Step 1: Failing hermetic bench test** with `httpx.MockTransport` (latency injected via `asyncio.sleep(0)` and a fixed clock).
- [x] **Step 2: Implement**; run `uv run --frozen pytest tests/tools/test_bench_free_pools.py -q` → PASS and `uv run --frozen pytest -m slow tests/slow/test_gateway_tpm_soak.py -q` → PASS.
- [x] **Step 3: Live dossier (operator-run, outside pytest):** `uv run --frozen python tools/gateway/bench_free_pools.py --pools keyless --minutes 10 --rpm 6 --out docs/research/2026-09-10-free-pool-benchmark.md`. The dossier records real latency, uptime, cap numbers, and a keep/kill line per pool; `kill` rows flip `enabled: false` in `seed/gateway-providers.yaml` via `pitwall gateway sync --apply-verdicts docs/research/2026-09-10-free-pool-benchmark.md`.
- [x] **Step 4: Commit**

```bash
git add tools/gateway/bench_free_pools.py tests/tools/test_bench_free_pools.py tests/slow/test_gateway_tpm_soak.py docs/research/2026-09-10-free-pool-benchmark.md seed/gateway-providers.yaml
git commit -s -m "feat(gateway): free-pool benchmark tool, TPM soak lane, and Phase 0 dossier"
```

---

### Task 18: `packages/gateway` fork — import, strip, shim, hardening, CI, release, attribution

**Files:**
- Create: `packages/gateway/` (`package.json`, `package-lock.json`, `tsconfig.json`, `src/shim.ts`, `src/config.ts`, `src/errors.ts`, `open-sse/` subset per §10.2, `NOTICE`, `README.md`, `CHANGELOG.md`, `scripts/import-upstream.sh`, `scripts/strip-list.txt`, `tests/shim.test.ts`, `tests/hardening.test.ts`)
- Create: `.github/workflows/gateway-ci.yml`, `.github/workflows/gateway-release.yml` (tags `gateway/v*`, mirrors `agent-routing-release.yml`)
- Modify: `NOTICE` (root, add OmniRoute attribution), `docs/decisions/0007-free-tier-gateway-tos-posture.md` (fill "Fork strip-list"), `docs/legal/gateway-attribution-review.md`, `pyproject.toml:94-120` (sdist `exclude` gains `/packages/gateway`), the five service `Dockerfile*` (verify `packages/` is not copied; add `packages/gateway` to `.dockerignore`)
- Create: `docs/sdlc/24-gateway.md`

**Interfaces:**
- Produces: `@pitwall/gateway` binary `dist/shim.js` serving exactly `POST /v1/chat/completions`, `GET /v1/models`, `POST /v1/embeddings`, `GET /health`, `GET /internal/telemetry` on `--bind 127.0.0.1 --port 20130`, bearer token from `PITWALL_GATEWAY_TOKEN` (required; boot fails without it), request body cap `1 MiB`, rate limit `120 rpm` per token, structured error envelope `{"error":{"type","message","request_id"}}` via upstream's `buildErrorBody`, compression policy header `x-pitwall-compression: off|rtk|caveman|stacked` (default `off`), and upstream translation for Claude/Gemini/Responses shapes inbound to OpenAI-shape outbound.

- [x] **Step 1: Import script and strip-list**

`packages/gateway/scripts/strip-list.txt` (one path per line, relative to the upstream root; the ADR "Fork strip-list" section is generated from this file):

```
src/
electron/
packages/browser-pool/
open-sse/mcp-server/
open-sse/vendor/
open-sse/utils/fingerprint*
open-sse/utils/cch*
open-sse/utils/zwj*
open-sse/executors/*-web.ts
open-sse/executors/claude-web/
open-sse/executors/chatgpt-web-codex/
open-sse/executors/antigravity*
open-sse/executors/adobe-firefly.ts
open-sse/services/browserBackedChat*
open-sse/services/browserPool.ts
open-sse/services/adobeFirefly*
open-sse/services/antigravity*
open-sse/services/chatgptWeb*
open-sse/handlers/imageGeneration*
open-sse/handlers/imageUpscale*
open-sse/handlers/mediaGeneration/
open-sse/handlers/musicGeneration.ts
open-sse/handlers/audio*
open-sse/handlers/search*
open-sse/handlers/cursorCliProxy.ts
docs/
public/
skills/
contrib/
examples/
```

`scripts/import-upstream.sh` clones `diegosouzapw/OmniRoute` at tag `v3.8.51`, copies `open-sse/` minus the strip-list into `packages/gateway/open-sse/`, replaces every `wreq-js` import with `globalThis.fetch`, records `UPSTREAM_SHA` and the list of kept files with SHA-256 into `packages/gateway/UPSTREAM.lock`, and fails if any stripped path survives (`grep -rl "wreq" open-sse && exit 1`).

- [x] **Step 2: Failing hardening tests** (`vitest`):

```ts
test("boot fails closed without PITWALL_GATEWAY_TOKEN", async () => {
  await expect(startShim({ env: {} })).rejects.toThrow(/PITWALL_GATEWAY_TOKEN/);
});
test("anonymous requests are 401 with the structured envelope", async () => {
  const res = await fetch(`${base}/v1/chat/completions`, { method: "POST", body: "{}" });
  expect(res.status).toBe(401);
  expect(await res.json()).toMatchObject({ error: { type: "authentication_error" } });
});
test("bodies over 1 MiB are 413", async () => {
  const body = JSON.stringify({ model: "m", messages: [{ role: "user", content: "x".repeat(1024 * 1024 + 1) }] });
  const res = await fetch(`${base}/v1/chat/completions`, { method: "POST", headers: auth, body });
  expect(res.status).toBe(413);
  expect(await res.json()).toMatchObject({ error: { type: "invalid_request_error" } });
});
test("non-loopback bind is refused", async () => {
  await expect(startShim({ env: { PITWALL_GATEWAY_TOKEN: "t" }, bind: "0.0.0.0" })).rejects.toThrow(/loopback/);
});
test("stack traces never leak", async () => {
  const res = await fetch(`${base}/v1/chat/completions`, { method: "POST", headers: auth,
    body: JSON.stringify({ model: "__throw__", messages: [] }) });   // the test executor throws on this model id
  expect(res.status).toBe(500);
  const text = await res.text();
  expect(text).not.toMatch(/\n\s+at /);
  expect(JSON.parse(text)).toMatchObject({ error: { type: "api_error", request_id: expect.any(String) } });
});
test("claude-shaped body is translated to openai-shape upstream", async () => {
  const upstream = captureUpstream();  // MSW handler recording the outbound body
  await fetch(`${base}/v1/chat/completions`, { method: "POST", headers: { ...auth, "x-pitwall-inbound-shape": "claude" },
    body: JSON.stringify({ model: "m", max_tokens: 8, system: "terse", messages: [{ role: "user", content: [{ type: "text", text: "hi" }] }] }) });
  expect(upstream.lastBody().messages[0]).toEqual({ role: "system", content: "terse" });
});
```

- [x] **Step 3: Implement `src/shim.ts`** (Node `http` server, no Next.js), `src/config.ts` (env parsing, fail-closed), `src/errors.ts` (re-export upstream `buildErrorBody`), route table limited to the five endpoints, `handleChatCore` and `handleEmbedding` from the kept upstream handlers, compression via `open-sse/services/compression` selected by the policy header.

- [x] **Step 4: CI and release** — `gateway-ci.yml` jobs: `lock` (`npm ci --ignore-scripts` and `npm audit --audit-level=high`), `lint` (`eslint` with the upstream config subset), `typecheck` (`tsc -p tsconfig.json`), `unit` (`vitest run`), `build`, `sbom` (`npx @cyclonedx/cyclonedx-npm --output-file gateway.cdx.json`). `gateway-release.yml` on `gateway/v*` tags: build, SBOM, attest, GitHub release. Run `uv run --frozen python tools/ci/check_workflows.py` → 0.

- [x] **Step 5: Attribution and docs** — root `NOTICE` gains "Pitwall Gateway is adapted from OmniRoute (MIT), © 2026 diegosouzapw; not affiliated with OmniRoute"; `packages/gateway/NOTICE` carries the full MIT text and the kept-file provenance; `docs/legal/gateway-attribution-review.md` records the dropped `wreq-js`/BoringSSL/ICU4X/Mozilla-CA notice chain; `docs/sdlc/24-gateway.md` documents surface, hardening, and the loopback-only contract; ADR 0007 "Fork strip-list" section is replaced with the strip-list contents and `UPSTREAM.lock` SHA.

- [x] **Step 6: Verify and commit**

Run (from `packages/gateway`): `npm ci --ignore-scripts && npm run typecheck && npm test && npm run build && npm audit --audit-level=high` → all exit 0. Root: `uv run --frozen python tools/ci/check_markdown_links.py` → 0; `uv run --frozen python tools/guards/repo_text_policy.py` → 0.

```bash
git add packages/gateway .github/workflows/gateway-ci.yml .github/workflows/gateway-release.yml NOTICE docs/decisions/0007-free-tier-gateway-tos-posture.md docs/legal/gateway-attribution-review.md docs/sdlc/24-gateway.md pyproject.toml .dockerignore
git commit -s -m "feat(gateway): fork the OmniRoute open-sse subset as @pitwall/gateway with a hardened loopback shim"
```

---

### Task 19: Retarget the adapter and supervisor to the fork; compression policy

**Files:**
- Modify: `src/pitwall/providers/gateway.py` (`GatewayCredentials.base_url` default stays loopback; `infer` sends `x-pitwall-compression` from `provider.config["gateway"].get("compression", "off")` and `Authorization` from `PITWALL_GATEWAY_TOKEN` when `base_url` is loopback), `src/pitwall/personal/gateway.py` (argv → `node packages/gateway/dist/shim.js --port 20130 --bind 127.0.0.1`), `src/pitwall/config.py` (`pitwall_gateway_url: str = "http://127.0.0.1:20130/v1"`, `pitwall_gateway_compression: Literal["off","rtk","caveman","stacked"] = "off"`), `seed/gateway-providers.yaml` via `tools/gateway/sync_catalog.py` (`base_url` for non-keyless rows becomes the fork URL; keyless rows may stay direct)
- Test: `tests/providers/test_gateway_provider.py` (extend), `tests/personal/test_gateway_supervisor.py` (extend), `tests/tools/test_gateway_sync_catalog.py` (extend)

- [x] **Step 1: Failing tests** — loopback requests carry the gateway token and the compression header; direct keyless requests carry neither; the extractor routes `recurring-*` rows through `http://127.0.0.1:20130/v1` and keyless rows direct when `--direct-keyless` is passed.
- [x] **Step 2: Implement**; re-run `uv run --frozen python tools/gateway/sync_catalog.py --version 3.8.51 --from-json <extract>` and `check_catalog_drift.py` → 0 (lock updated in the same commit).
- [x] **Step 3: Verify and commit**

Run: `uv run --frozen pytest tests/providers tests/personal tests/tools -q` → PASS.

```bash
git add src/pitwall/providers/gateway.py src/pitwall/personal/gateway.py src/pitwall/config.py tools/gateway/sync_catalog.py seed/gateway-providers.yaml config/gateway-catalog.json config/gateway-catalog.lock.json tests/providers/test_gateway_provider.py tests/personal/test_gateway_supervisor.py tests/tools/test_gateway_sync_catalog.py
git commit -s -m "feat(gateway): route budgeted pools through the fork with a compression policy"
```

---

### Task 20: Compose the prongs — cross-prong what-if, Autopilot shadow proposal, cache-affinity pinning

**Files:**
- Modify: `src/pitwall/cost/simulator.py:60-300` (`WhatIfProjection` gains `prong_comparison: tuple[ProngOption, ...]`; `WhatIfSimulator.project` computes own-pod $/token from `pitwall.models.fit_options`, metered $/token from `PerTokenPricing`, and free burn-down from `QuotaSnapshot`), `src/pitwall/autopilot/schema.py:42-50` (`AutopilotActionKind.PROPOSE_SEAT_SWITCH`, `PROPOSE_SERVE`), `src/pitwall/autopilot/controller.py` (shadow mode emits proposals from `escape_hatch_message` and the prong comparison; never applies them)
- Create: `src/pitwall/routing/affinity.py` (`affinity_bonus(provider, hints) -> float`: `+5.0` when `hints.cache_key` matches the provider's last-served `cache_key` in `ObservedMetrics`, for pools flagged `prompt_cache: true` in catalog evidence), `src/pitwall/routing/types.py` (`Hints.cache_key: str | None`, `ScoreExplanation.affinity_bonus`)
- Test: `tests/cost/test_simulator_prongs.py`, `tests/autopilot/test_shadow_proposals.py`, `tests/routing/test_affinity.py`

- [x] **Step 1: Failing tests** — a 10M-token day projects own-pod at `$X`, DeepSeek metered at `$Y`, free burn-down covering `Z%`; Autopilot in shadow mode yields one `PROPOSE_SERVE` action with `executed=False` when all free pools are exhausted; `affinity_bonus` is `5.0` on a cache-key match and `0.0` otherwise, and plan identity changes with `hints.cache_key`.
- [x] **Step 2: Implement** `routing/affinity.py`:

```python
"""Cache-affinity pinning for prompt-cache-heavy pools (research §14 Phase 4)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

AFFINITY_BONUS = 5.0


def affinity_bonus(provider: Any, hints: Any, observed: Any) -> float:
    if hints is None or getattr(hints, "cache_key", None) is None:
        return 0.0
    config = getattr(provider, "config", None) or (
        provider.get("config") if isinstance(provider, Mapping) else {}
    )
    catalog = (
        ((config.get("gateway") or {}).get("catalog") or {}) if isinstance(config, Mapping) else {}
    )
    if not bool(catalog.get("prompt_cache", False)):
        return 0.0
    last_key = getattr(observed, "last_cache_key", None)
    return AFFINITY_BONUS if last_key == hints.cache_key else 0.0
```

`scoring.explain_score` adds `affinity_bonus = affinity_bonus(provider, active_hints, active_observed)` into `score_before_multiplier` and exposes it in `ScoreExplanation`; `ObservedMetrics` gains `last_cache_key: str | None = None`. `WhatIfProjection.prong_comparison` rows are `ProngOption(prong: Literal["own_serve","free","metered"], usd_per_million_tokens: Decimal, coverage_pct: float, note: str)`; the Autopilot shadow path builds `AutopilotAction(kind=PROPOSE_SERVE, executed=False, detail=escape_hatch_message(...))` and `AutopilotAction(kind=PROPOSE_SEAT_SWITCH, executed=False, detail=f"metered {p.usd_per_million_tokens}/M beats own-pod")` when the comparison favors it; `AutopilotController` never applies either kind. Keep every new term inside `ScoreExplanation.to_dict()` so plans stay inspectable.
- [x] **Step 3: Verify and commit**

Run: `uv run --frozen pytest tests/cost tests/autopilot tests/routing -q` → PASS.

```bash
git add src/pitwall/cost/simulator.py src/pitwall/autopilot/schema.py src/pitwall/autopilot/controller.py src/pitwall/routing/affinity.py src/pitwall/routing/types.py src/pitwall/routing/scoring.py tests/cost/test_simulator_prongs.py tests/autopilot/test_shadow_proposals.py tests/routing/test_affinity.py
git commit -s -m "feat(prongs): cross-prong what-if, Autopilot shadow proposals, and cache-affinity pinning"
```

---

### Task 21: Documentation sweep and final gates

**Files:**
- Modify: `docs/sdlc/02-api-rest.md` (§3 route inventory: `/v1/quotas`, `/v1/admin/quotas/refresh`, `/v1/gateway/models`, `/v1/messages`), `docs/sdlc/04-routing.md` (§2 new modules `quota`, `lockout`, `zero_cost`, `cascade_seed`, `affinity`; §3 pipeline Stage 2 gains `quota? lockout? zero-cost?` and Stage 3 gains the new terms), `docs/sdlc/05-cost-budget.md` (§3 zero pricing burn-down), `docs/sdlc/07-data-model-db.md` (migration order 0033; three table details), `docs/sdlc/10-reconciler-lifecycle.md` (§2 `_quota_poll`), `docs/sdlc/13-observability.md` (free-pool metrics), `docs/sdlc/18-cli.md` (§3 inventory rows for `gateway`, `quotas`, `serve --gateway`; TUI addenda), `docs/sdlc/20-provider-plugins.md` (`openai_gateway` row in the capability table at `:65`), `docs/sdlc/23-agent-routing.md` (gateway seat), `docs/support-matrix.md` (gateway component rows mirroring `:31-33`), `docs/operator/current-providers.md` (gateway row: credential `PITWALL_GATEWAY_API_KEY` optional, source `gateway-catalog-2026-09-10`), `README.md` (component list), `docs/research/2026-09-08-omniroute-free-tier-integration.md` (status → "Implemented; see plan")

- [x] **Step 1: Write every section listed above** (each SDLC section follows its file's existing `## n.` numbering and table style).
- [x] **Step 2: Run the full gates**

```
uv run --frozen ruff check . && uv run --frozen ruff format --check . && uv run --frozen mypy --strict src/
uv run --frozen pytest -q -m "not integration and not slow" -p no:randomly 2>&1 | tail -3
make up && make test-db 2>&1 | tail -3
uv run --frozen python tools/ci/check_markdown_links.py && uv run --frozen python tools/gateway/check_catalog_drift.py && make openapi-check
cd packages/agent-routing && python3 -m unittest discover -s tests 2>&1 | tail -3 && .venv/bin/ruff check runtime tests && .venv/bin/mypy --python-version 3.14 runtime/model_routing
cd packages/gateway && npm ci --ignore-scripts && npm test && npm run build
```

Expected: every command exits 0; pytest prints `passed` with `0 failed`; the component suite prints `OK`.

- [x] **Step 3: Commit**

```bash
git add docs README.md
git commit -s -m "docs: free-tier gateway across SDLC, support matrix, operator, and README"
```

---

## Exit checks (proof per research section)

| Research item | Proof command |
|---|---|
| §8 catalog sync + drift gate | `uv run --frozen pytest tests/tools -q`; `uv run --frozen python tools/gateway/check_catalog_drift.py` |
| §9.1 adapter (keyless, 429 typed, SSRF) | `uv run --frozen pytest tests/providers/test_gateway_provider.py tests/security/test_gateway_credentials.py -q` |
| §9.2 migrations | `make test-db` |
| §9.3 quota gate truth table + replay identity | `uv run --frozen pytest tests/routing/test_quota_gate.py tests/routing/test_planning_context.py -q` |
| §9.4 lockout backoff table | `uv run --frozen pytest tests/routing/test_lockout.py -q` |
| §9.5 zero-cost evidence matrix | `uv run --frozen pytest tests/routing/test_zero_cost.py -q` |
| §9.6 ladder, descent, escape hatch | `uv run --frozen pytest tests/routing/test_cascade_seed.py -q` |
| §9.7 surfaces (API, CLI, TUI, MCP) | `uv run --frozen pytest tests/api tests/cli tests/tui tests/mcp -q -p no:randomly` |
| §9.8 soak: zero paid leakage, bounded 429 | `uv run --frozen pytest -m slow tests/slow/test_gateway_tpm_soak.py -q` |
| §10 fork hardening | `cd packages/gateway && npm test` |
| §14 Phase 0 dossier | `docs/research/2026-09-10-free-pool-benchmark.md` exists with a keep/kill line per pool |
| Decisions Q1–Q5 | Task 11 (Q1), Task 12 (Q2, Q4), Task 3 (Q3), Task 1 (Q5) |
