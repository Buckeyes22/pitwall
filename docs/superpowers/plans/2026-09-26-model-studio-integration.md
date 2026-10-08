# Alibaba Cloud Model Studio Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Alibaba Cloud Model Studio (Token Plan and pay-as-you-go) a first-class provider in Agent Routing and in the broker, sharing one committed catalog.

**Architecture:** One curated JSON catalog (`model-studio.json`) ships byte-identical in both packages, and a root test fails on drift. Agent Routing (stdlib only) gains a `model-studio` endpoint kind, catalog-derived limits and effort, harness sync, a Token Plan automation gate, per-endpoint concurrency slots, and readiness via an ACS3-signed OpenAPI read. The broker gains a `model_studio` provider type and adapter (streaming, usage, code-keyed 429 classification), catalog-derived pricing with cached-input and context tiers, a Credits quota window in `provider_quotas`, and proxy support through `openai_base_url`.

**Tech Stack:** Python 3.14.7; Agent Routing: stdlib, `unittest`; broker: pydantic, httpx, asyncpg, pytest; Postgres migrations in `db/migrations`.

**Spec:** `docs/superpowers/specs/2026-09-26-model-studio-integration-design.md` (amended in the same commit as this plan; the amendments are listed at the end of this plan).

## Global Constraints

- Python 3.14.7 only. Run Python through `uv run` (root) or `.venv/bin/python` (`packages/agent-routing`); never bare `python`.
- The root project and `packages/agent-routing` are independent uv projects. The two halves never import each other.
- Agent Routing runtime code is standard-library-only (`urllib`, `hmac`, `hashlib`, `fcntl`, `json`); no new dependencies anywhere.
- Only credential names are stored (`apiKeyEnv`, `credential_ref`, env var names); no error message, log line, or test output may contain a key, secret, or signature input secret.
- Token Plan keys begin `sk-sp-`; Token Plan base URLs are `https://token-plan.ap-southeast-1.maas.aliyuncs.com/{compatible-mode/v1,apps/anthropic,api/v1}`; Token Plan is `ap-southeast-1` only.
- Non-interactive Token Plan use requires `MODEL_STUDIO_TOKEN_PLAN_AUTOMATION=accept` (broker) or endpoint `tokenPlanAutomation: "accept"` (Agent Routing). Without it: refuse and name the setting and the Token Plan terms.
- OpenAPI: host `modelstudio.ap-southeast-1.aliyuncs.com`, version `2026-02-10`, ACS3-HMAC-SHA256, AccessKey from `ALIBABA_CLOUD_ACCESS_KEY_ID` / `ALIBABA_CLOUD_ACCESS_KEY_SECRET`; reads only.
- Per-minute 429s cool down 60 seconds; Token Plan exhaustion locks until the next renewal; billing states lock one hour with reason `billing_state`.
- Tests are hermetic: no real Model Studio host is contacted unless `PITWALL_MODEL_STUDIO_LIVE=1` and `--run-live` are both set (Task 19 only).
- Pipe long test output through `| tail -40`. Commit after every task; never weaken an existing test to pass. Existing tests that pin a list this work extends (registry ids, migration head, live-guard hosts, schema count) are updated to the new value, not deleted.
- Private text policy: no personal names, hostnames, or addresses in committed text; run the guard with the private overlay before every commit that touches docs.

## Review Focus

1. A `routes.json` written by `routes add-model-studio-endpoint` and loaded again round-trips unchanged (the derived `baseUrl` is persisted and must re-validate). Pinned in Task 4 (`test_saved_endpoint_round_trips`).
2. A pay-as-you-go key (`sk-…`, not `sk-sp-`) configured on a Token Plan endpoint is refused before any request leaves the machine, in both halves, without echoing the key. Pinned in Tasks 3, 7, 11, and 13.
3. A thinking request with only `max_tokens` cannot spend past the admitted ceiling: the adapter converts it to `max_completion_tokens` (or keeps `max_tokens` for models whose `max_tokens` already includes reasoning), and the proxy does the same. Pinned in Tasks 13 and 15.
4. A crashed dispatch never leaks a concurrency slot. Pinned in Task 7 (`test_slot_is_released_when_holder_process_dies`).
5. A Model Studio 429 whose body says "Allocated quota exceeded" locks the model for 60 seconds, not until the first of next month, and the generic gateway classifier no longer treats a bare "quota" as monthly. Pinned in Tasks 1 and 13.

## Execution lanes

Tasks 1–2 run first (Task 2 creates the shared catalog). Then two lanes may run in parallel with disjoint files; Tasks 17–19 run after both lanes finish.

| Lane | Tasks | Exclusive files |
| --- | --- | --- |
| A — Agent Routing | 3, 4, 5, 6, 7, 8, 9 | `packages/agent-routing/**` except `resources/config/model-studio.json` (read-only after Task 2) |
| B — Broker | 10, 11, 12, 13, 14, 15, 16 | `src/pitwall/**`, `db/migrations/0035_*`, `tests/**` (root), `docs/api/openapi-baseline.json`, `.env.example` |

Lane prompts start from `~/.claude/templates/lane-prompt.md`. Neither lane edits `docs/`, `CHANGELOG*`, or the other lane's files.

## File structure

Agent Routing (`packages/agent-routing/`):
- `runtime/model_routing/resources/config/model-studio.json` — canonical catalog (plans, regions, paths, OpenAPI, models, errors).
- `runtime/model_routing/resources/schemas/model-studio.schema.json` — JSON Schema for the catalog.
- `runtime/model_routing/model_studio.py` — catalog lookups and the four validation rules; base URL derivation; error classification; renewal window.
- `runtime/model_routing/model_studio_openapi.py` — stdlib ACS3 signer and `GetSubscriptionStats` read.
- `runtime/model_routing/endpoint_slots.py` — per-endpoint `flock` slots and the local exhaustion lockout file.
- Modified: `routes.py`, `cli.py`, `providers/opencode.py`, `providers/pi.py`, `route_probe.py`, `resources/schemas/routes.schema.json`, `tools/validate_json_schemas.py`, `tools/release/inspect_artifacts.py`.

Broker (repo root):
- `src/pitwall/providers/model_studio/__init__.py` — exports.
- `src/pitwall/providers/model_studio/catalog.json` — byte-identical catalog copy.
- `src/pitwall/providers/model_studio/catalog.py` — catalog lookups, validation rules, pricing derivation, error classification, proxy body rewrite.
- `src/pitwall/providers/model_studio/openapi.py` — ACS3 signer (httpx), subscription stats, billing overview.
- `src/pitwall/providers/model_studio/adapter.py` — `ModelStudioProvider`.
- `db/migrations/0035_model_studio.sql`.
- Modified: `core/enums.py`, `providers/registry.py`, `providers/__init__.py`, `providers/gateway.py`, `routing/lockout.py`, `routing/fallback.py`, `cost/estimator.py`, `cost/usage.py`, `routing/production.py`, `seed.py`, `api/provider_schemas.py`, `reconciler/__init__.py`, `db/quota_repository.py`, `tests/conftest.py`.

---

### Task 1: Generic `classify_429` stops treating a bare "quota" as monthly

**Files:**
- Modify: `src/pitwall/providers/gateway.py:57` (`_MONTHLY`)
- Test: `tests/providers/test_gateway_provider.py`

**Interfaces:**
- Consumes: `classify_429(status, body, headers, *, now) -> tuple[QuotaReason, dt.datetime | None]`.
- Produces: same signature; bare "quota" wording now yields `("rate_limit_exceeded", None)`.

- [ ] **Step 1: Write the failing tests** (append to `tests/providers/test_gateway_provider.py`)

```python
def test_classify_429_bare_quota_is_a_short_rate_limit_not_a_monthly_lockout() -> None:
    reason, reset = classify_429(
        429, "Allocated quota exceeded, please increase your quota limit.", {}, now=NOW
    )
    assert (reason, reset) == ("rate_limit_exceeded", None)


@pytest.mark.parametrize(
    "body",
    [
        "monthly limit reached",
        "you have used your quota for this month",
        "5,000,000 tokens per month exceeded",
    ],
)
def test_classify_429_explicit_monthly_wording_still_locks_until_next_month(body: str) -> None:
    reason, reset = classify_429(429, body, {}, now=NOW)
    assert (reason, reset) == ("quota_exhausted", dt.datetime(2026, 10, 1, tzinfo=dt.UTC))
```

- [ ] **Step 2: Run to verify the first test fails**

Run: `uv run pytest tests/providers/test_gateway_provider.py -q -k "bare_quota or explicit_monthly" 2>&1 | tail -15`
Expected: `test_classify_429_bare_quota_is_a_short_rate_limit_not_a_monthly_lockout` FAILS with `('quota_exhausted', datetime(2026, 10, 1, ...)) != ('rate_limit_exceeded', None)`; the monthly cases pass except `per month` (FAIL).

- [ ] **Step 3: Implement**

In `src/pitwall/providers/gateway.py` replace line 57:

```python
_MONTHLY = re.compile(r"\b(monthly|this month|per month)\b", re.I)
```

- [ ] **Step 4: Run the whole gateway test file**

Run: `uv run pytest tests/providers/test_gateway_provider.py -q 2>&1 | tail -5`
Expected: all pass, `0 failed`.

- [ ] **Step 5: Commit**

```bash
git add src/pitwall/providers/gateway.py tests/providers/test_gateway_provider.py
git commit -m "fix(gateway): a bare quota 429 is a short backoff, not a monthly lockout"
```

---

### Task 2: The shared catalog, its schema, and the drift test

**Files:**
- Create: `packages/agent-routing/runtime/model_routing/resources/config/model-studio.json`
- Create: `packages/agent-routing/runtime/model_routing/resources/schemas/model-studio.schema.json`
- Modify: `packages/agent-routing/tools/validate_json_schemas.py` (schema count 8→9; validate the catalog)
- Modify: `packages/agent-routing/tools/release/inspect_artifacts.py:118-121` (packaged config allowlist)
- Create: `src/pitwall/providers/model_studio/__init__.py` (empty module docstring for now)
- Create: `src/pitwall/providers/model_studio/catalog.json` (byte-identical copy)
- Test: `packages/agent-routing/tests/test_model_studio_catalog.py`, `tests/test_model_studio_catalog_parity.py`

**Interfaces:**
- Produces: resource `config/model-studio.json` readable through `model_routing.resources.read_resource_json`; package data `pitwall/providers/model_studio/catalog.json` readable through `importlib.resources`.

- [ ] **Step 1: Write the catalog** (`packages/agent-routing/runtime/model_routing/resources/config/model-studio.json`)

Prices are USD per million tokens for Singapore (International scope), copied from the model pages' pricing tables in the research snapshot. Tier thresholds use the pages' `k` = 1,000.

```json
{
  "schemaVersion": 1,
  "snapshot": "2026-09-26",
  "sources": ["token-plan-overview", "base-url", "regions", "error-code", "context-cache"],
  "paths": {"openai": "/compatible-mode/v1", "anthropic": "/apps/anthropic", "native": "/api/v1"},
  "openApi": {
    "host": "modelstudio.ap-southeast-1.aliyuncs.com",
    "version": "2026-02-10",
    "sources": ["get-subscription-stats", "api-modelstudio-2026-02-10-getbillingoverview"]
  },
  "plans": {
    "token-plan-personal": {
      "family": "token-plan",
      "edition": "personal",
      "regions": ["ap-southeast-1"],
      "keyPrefix": "sk-sp-",
      "unit": "credits",
      "cycleDays": 30,
      "exhaustion": "blocking",
      "baseUrls": {
        "openai": "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
        "anthropic": "https://token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic",
        "native": "https://token-plan.ap-southeast-1.maas.aliyuncs.com/api/v1"
      },
      "tiers": {
        "lite": {"credits": 11500, "concurrency": [1, 2]},
        "essential": {"credits": 25500, "concurrency": [2, 3]},
        "standard": {"credits": 45000, "concurrency": [3, 4]},
        "pro": {"credits": 180000, "concurrency": [6, 8]}
      },
      "sources": ["token-plan-overview", "token-plan-personal-overview", "token-plan-personal-faq", "token-plan-personal-quick-start"]
    },
    "token-plan-team": {
      "family": "token-plan",
      "edition": "team",
      "regions": ["ap-southeast-1"],
      "keyPrefix": "sk-sp-",
      "unit": "credits",
      "cycleDays": 30,
      "exhaustion": "blocking",
      "baseUrls": {
        "openai": "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
        "anthropic": "https://token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic",
        "native": "https://token-plan.ap-southeast-1.maas.aliyuncs.com/api/v1"
      },
      "tiers": {
        "standard": {"credits": 25000, "concurrency": null},
        "advanced": {"credits": 100000, "concurrency": null},
        "premium": {"credits": 250000, "concurrency": null}
      },
      "sources": ["token-plan-team-overview", "token-plan-team-faq", "token-plan-team-quickstart"]
    },
    "pay-as-you-go": {
      "family": "pay-as-you-go",
      "edition": null,
      "regions": ["ap-southeast-1", "cn-beijing", "us-east-1", "eu-central-1", "ap-northeast-1", "cn-hongkong"],
      "keyPrefix": null,
      "unit": "usd",
      "cycleDays": null,
      "exhaustion": "billing",
      "baseUrls": null,
      "tiers": null,
      "sources": ["get-api-key", "regions", "base-url", "budget-management"]
    }
  },
  "regions": {
    "ap-southeast-1": {"workspaceHost": "{workspace}.ap-southeast-1.maas.aliyuncs.com", "legacyHost": "dashscope-intl.aliyuncs.com", "priced": true},
    "cn-beijing": {"workspaceHost": "{workspace}.cn-beijing.maas.aliyuncs.com", "legacyHost": "dashscope.aliyuncs.com", "priced": false},
    "us-east-1": {"workspaceHost": "{workspace}.us-east-1.maas.aliyuncs.com", "legacyHost": "dashscope-us.aliyuncs.com", "priced": false},
    "eu-central-1": {"workspaceHost": "{workspace}.eu-central-1.maas.aliyuncs.com", "legacyHost": null, "priced": false},
    "ap-northeast-1": {"workspaceHost": "{workspace}.ap-northeast-1.maas.aliyuncs.com", "legacyHost": null, "priced": false},
    "cn-hongkong": {"workspaceHost": "{workspace}.cn-hongkong.maas.aliyuncs.com", "legacyHost": "cn-hongkong.dashscope.aliyuncs.com", "priced": false}
  },
  "models": {
    "auto": {"kind": "text", "contextTokens": null, "maxInputTokens": null, "maxInputTokensThinking": null, "maxOutputTokens": null, "maxReasoningTokens": null, "inputs": ["text"], "reasoning": "router", "effort": null, "maxTokensIncludesReasoning": false, "eligibility": {"personal": true, "team": true, "payAsYouGo": false}, "prices": null, "sources": ["token-plan-personal-overview"]},
    "qwen3.8-max": {"kind": "text", "contextTokens": 1000000, "maxInputTokens": 991808, "maxInputTokensThinking": 983616, "maxOutputTokens": 131072, "maxReasoningTokens": 262144, "inputs": ["text", "image", "video"], "reasoning": "hybrid-on", "effort": {"param": "reasoning_effort", "values": ["xhigh", "medium", "low"], "default": "xhigh", "exclusiveWith": ["thinking_budget"]}, "maxTokensIncludesReasoning": false, "eligibility": {"personal": true, "team": true, "payAsYouGo": true}, "prices": {"input": "2", "output": "6", "cachedInput": "0.25"}, "sources": ["qwen3-8-max", "qwen-api-via-openai-chat-completions"]},
    "qwen3.8-flash": {"kind": "text", "contextTokens": 1000000, "maxInputTokens": 991808, "maxInputTokensThinking": 983616, "maxOutputTokens": 131072, "maxReasoningTokens": 262144, "inputs": ["text", "image", "video"], "reasoning": "hybrid-on", "effort": {"param": "reasoning_effort", "values": ["xhigh", "medium", "low"], "default": "xhigh", "exclusiveWith": ["thinking_budget"]}, "maxTokensIncludesReasoning": false, "eligibility": {"personal": true, "team": true, "payAsYouGo": true}, "prices": {"input": "0.15", "output": "0.47", "cachedInput": "0.016"}, "sources": ["qwen3-8-flash", "qwen-api-via-openai-chat-completions"]},
    "qwen3.7-max": {"kind": "text", "contextTokens": 1000000, "maxInputTokens": 991808, "maxInputTokensThinking": 983616, "maxOutputTokens": 131072, "maxReasoningTokens": null, "inputs": ["text"], "reasoning": "hybrid-on", "effort": null, "maxTokensIncludesReasoning": false, "eligibility": {"personal": true, "team": true, "payAsYouGo": true}, "prices": {"input": "2.5", "output": "7.5", "cachedInput": "0.5"}, "sources": ["qwen3-7-max"]},
    "qwen3.7-plus": {"kind": "text", "contextTokens": 1000000, "maxInputTokens": 991808, "maxInputTokensThinking": 983616, "maxOutputTokens": 131072, "maxReasoningTokens": null, "inputs": ["text", "image", "video"], "reasoning": "hybrid-on", "effort": null, "maxTokensIncludesReasoning": false, "eligibility": {"personal": true, "team": true, "payAsYouGo": true}, "prices": {"input": "0.4", "output": "1.6", "cachedInput": "0.08", "tiers": [{"aboveInputTokens": 256000, "input": "1.2", "output": "4.8", "cachedInput": "0.24"}]}, "sources": ["qwen3-7-plus"]},
    "qwen3.6-plus": {"kind": "text", "contextTokens": 1000000, "maxInputTokens": 991808, "maxInputTokensThinking": 983616, "maxOutputTokens": 65536, "maxReasoningTokens": 81920, "inputs": ["text", "image", "video"], "reasoning": "hybrid-on", "effort": null, "maxTokensIncludesReasoning": false, "eligibility": {"personal": false, "team": true, "payAsYouGo": true}, "prices": {"input": "0.5", "output": "3", "cachedInput": null, "tiers": [{"aboveInputTokens": 256000, "input": "2", "output": "6", "cachedInput": null}]}, "sources": ["qwen3-6-plus"]},
    "qwen3.6-flash": {"kind": "text", "contextTokens": 1000000, "maxInputTokens": 991808, "maxInputTokensThinking": 983616, "maxOutputTokens": 65536, "maxReasoningTokens": null, "inputs": ["text", "image", "video"], "reasoning": "hybrid-on", "effort": null, "maxTokensIncludesReasoning": false, "eligibility": {"personal": true, "team": true, "payAsYouGo": true}, "prices": {"input": "0.25", "output": "1.5", "cachedInput": null, "tiers": [{"aboveInputTokens": 256000, "input": "1", "output": "4", "cachedInput": null}]}, "sources": ["qwen3-6-flash"]},
    "deepseek-v4.1-flash": {"kind": "text", "contextTokens": 1000000, "maxInputTokens": 1000000, "maxInputTokensThinking": 1000000, "maxOutputTokens": 393216, "maxReasoningTokens": null, "inputs": ["text", "image"], "reasoning": "hybrid-on", "effort": {"param": "reasoning_effort", "values": ["max", "high", "low"], "default": "high", "exclusiveWith": []}, "maxTokensIncludesReasoning": true, "eligibility": {"personal": true, "team": true, "payAsYouGo": true}, "prices": {"input": "0.3", "output": "1.2", "cachedInput": "0.03", "note": "busy-hour prices; idle hours cost half"}, "sources": ["deepseek-v4-1-flash"]},
    "deepseek-v4-pro": {"kind": "text", "contextTokens": 1000000, "maxInputTokens": 1000000, "maxInputTokensThinking": 1000000, "maxOutputTokens": 393216, "maxReasoningTokens": null, "inputs": ["text"], "reasoning": "hybrid-on", "effort": null, "maxTokensIncludesReasoning": true, "eligibility": {"personal": true, "team": true, "payAsYouGo": true}, "prices": {"input": "2.4", "output": "4.8", "cachedInput": "0.2"}, "sources": ["deepseek-v4-pro"]},
    "deepseek-v4-pro-0813": {"kind": "text", "contextTokens": 1000000, "maxInputTokens": 1000000, "maxInputTokensThinking": 1000000, "maxOutputTokens": 393216, "maxReasoningTokens": null, "inputs": ["text"], "reasoning": "hybrid-on", "effort": null, "maxTokensIncludesReasoning": true, "eligibility": {"personal": true, "team": true, "payAsYouGo": true}, "prices": null, "sources": ["deepseek-v4-pro", "quota-management"]},
    "deepseek-v4-flash": {"kind": "text", "contextTokens": 1000000, "maxInputTokens": 1000000, "maxInputTokensThinking": 1000000, "maxOutputTokens": 393216, "maxReasoningTokens": null, "inputs": ["text"], "reasoning": "hybrid-on", "effort": null, "maxTokensIncludesReasoning": true, "eligibility": {"personal": false, "team": true, "payAsYouGo": true}, "prices": {"input": "0.2", "output": "0.4", "cachedInput": "0.04"}, "sources": ["deepseek-v4-flash"]},
    "deepseek-v4-flash-0731": {"kind": "text", "contextTokens": 1000000, "maxInputTokens": 1000000, "maxInputTokensThinking": 1000000, "maxOutputTokens": 393216, "maxReasoningTokens": null, "inputs": ["text"], "reasoning": "hybrid-on", "effort": null, "maxTokensIncludesReasoning": true, "eligibility": {"personal": true, "team": true, "payAsYouGo": true}, "prices": null, "sources": ["deepseek-v4-flash"]},
    "deepseek-v3.2": {"kind": "text", "contextTokens": 131072, "maxInputTokens": 98304, "maxInputTokensThinking": null, "maxOutputTokens": 65536, "maxReasoningTokens": null, "inputs": ["text"], "reasoning": "hybrid-off", "effort": null, "maxTokensIncludesReasoning": false, "eligibility": {"personal": false, "team": true, "payAsYouGo": true}, "prices": {"input": "0.57", "output": "1.71", "cachedInput": "0.114"}, "sources": ["deepseek-v3-3"]},
    "kimi-k2.7-code": {"kind": "text", "contextTokens": 262144, "maxInputTokens": 229376, "maxInputTokensThinking": null, "maxOutputTokens": 16384, "maxReasoningTokens": null, "inputs": ["text", "image", "video"], "reasoning": "always", "effort": null, "maxTokensIncludesReasoning": false, "eligibility": {"personal": false, "team": true, "payAsYouGo": true}, "prices": {"input": "0.95", "output": "4", "cachedInput": "0.19"}, "sources": ["kimi-k2-7-code"]},
    "kimi-k2.6": {"kind": "text", "contextTokens": 262144, "maxInputTokens": 229376, "maxInputTokensThinking": null, "maxOutputTokens": 16384, "maxReasoningTokens": null, "inputs": ["text", "image", "video"], "reasoning": "hybrid-off", "effort": null, "maxTokensIncludesReasoning": false, "eligibility": {"personal": false, "team": true, "payAsYouGo": true}, "prices": null, "sources": ["kimi-k2-6"]},
    "kimi-k2.5": {"kind": "text", "contextTokens": 262144, "maxInputTokens": 229376, "maxInputTokensThinking": null, "maxOutputTokens": 16384, "maxReasoningTokens": null, "inputs": ["text", "image", "video"], "reasoning": "hybrid-off", "effort": null, "maxTokensIncludesReasoning": false, "eligibility": {"personal": false, "team": true, "payAsYouGo": true}, "prices": null, "sources": ["kimi-k2-5"]},
    "glm-5.3": {"kind": "text", "contextTokens": 1048576, "maxInputTokens": 1048576, "maxInputTokensThinking": null, "maxOutputTokens": 131072, "maxReasoningTokens": null, "inputs": ["text"], "reasoning": "always", "effort": {"param": "reasoning_effort", "values": ["max", "high", "low"], "default": "max", "exclusiveWith": []}, "maxTokensIncludesReasoning": true, "eligibility": {"personal": true, "team": true, "payAsYouGo": true}, "prices": {"input": "1.4", "output": "4.4", "cachedInput": "0.28"}, "sources": ["glm-5-3"]},
    "glm-5.2": {"kind": "text", "contextTokens": 1048576, "maxInputTokens": 1048576, "maxInputTokensThinking": null, "maxOutputTokens": 131072, "maxReasoningTokens": null, "inputs": ["text"], "reasoning": "hybrid-on", "effort": null, "maxTokensIncludesReasoning": false, "eligibility": {"personal": true, "team": true, "payAsYouGo": true}, "prices": {"input": "1.4", "output": "4.4", "cachedInput": "0.28"}, "sources": ["glm-5-2"]},
    "glm-5.1": {"kind": "text", "contextTokens": 202745, "maxInputTokens": 202745, "maxInputTokensThinking": 169984, "maxOutputTokens": 131072, "maxReasoningTokens": null, "inputs": ["text"], "reasoning": "hybrid-on", "effort": null, "maxTokensIncludesReasoning": false, "eligibility": {"personal": false, "team": true, "payAsYouGo": true}, "prices": {"input": "1.4", "output": "4.4", "cachedInput": "0.26"}, "sources": ["glm-5-1"]},
    "glm-5": {"kind": "text", "contextTokens": 202752, "maxInputTokens": 169984, "maxInputTokensThinking": null, "maxOutputTokens": 16384, "maxReasoningTokens": null, "inputs": ["text"], "reasoning": "hybrid-on", "effort": null, "maxTokensIncludesReasoning": false, "eligibility": {"personal": false, "team": true, "payAsYouGo": true}, "prices": null, "sources": ["glm-9"]},
    "MiniMax-M2.5": {"kind": "text", "contextTokens": 204800, "maxInputTokens": 196608, "maxInputTokensThinking": null, "maxOutputTokens": 32768, "maxReasoningTokens": null, "inputs": ["text"], "reasoning": "always", "effort": null, "maxTokensIncludesReasoning": false, "eligibility": {"personal": false, "team": true, "payAsYouGo": true}, "prices": null, "sources": ["minimax-m2-5"]},
    "qwen-image-3.0-pro": {"kind": "image", "eligibility": {"personal": true, "team": true, "payAsYouGo": true}, "sources": ["qwen-image-3-0-pro"]},
    "qwen-image-2.0": {"kind": "image", "eligibility": {"personal": false, "team": true, "payAsYouGo": true}, "sources": ["qwen-image-2-0"]},
    "qwen-image-2.0-pro": {"kind": "image", "eligibility": {"personal": false, "team": true, "payAsYouGo": true}, "sources": ["qwen-image-2-0-pro"]},
    "wan2.7-image": {"kind": "image", "eligibility": {"personal": true, "team": true, "payAsYouGo": true}, "sources": ["wan2-7-image"]},
    "wan2.7-image-pro": {"kind": "image", "eligibility": {"personal": true, "team": true, "payAsYouGo": true}, "sources": ["wan2-7-image-pro"]},
    "happyhorse-1.1-t2v": {"kind": "video", "eligibility": {"personal": true, "team": true, "payAsYouGo": true}, "sources": ["happyhorse-1-1-t2v"]},
    "happyhorse-1.1-i2v": {"kind": "video", "eligibility": {"personal": true, "team": true, "payAsYouGo": true}, "sources": ["happyhorse-1-1-i2v"]},
    "happyhorse-1.1-r2v": {"kind": "video", "eligibility": {"personal": true, "team": true, "payAsYouGo": true}, "sources": ["happyhorse-1-1-r2v"]},
    "qwen-audio-3.0-tts-plus": {"kind": "speech", "eligibility": {"personal": true, "team": true, "payAsYouGo": true}, "sources": ["qwen-audio-3-0-tts-plus"]},
    "qwen-audio-3.0-realtime-plus": {"kind": "realtime", "eligibility": {"personal": true, "team": true, "payAsYouGo": true}, "sources": ["qwen-audio-3-0-realtime-plus"]},
    "qwen-audio-3.0-asr-flash": {"kind": "speech", "eligibility": {"personal": true, "team": true, "payAsYouGo": true}, "sources": ["qwen-audio-3-0-asr-flash"]}
  },
  "errors": [
    {"status": 429, "code": "Throttling.RateQuota", "classification": "rate_limit", "cooldownSeconds": 60},
    {"status": 429, "code": "Throttling.BurstRate", "classification": "rate_limit", "cooldownSeconds": 60},
    {"status": 429, "code": "Throttling.AllocationQuota", "classification": "rate_limit", "cooldownSeconds": 60},
    {"status": 429, "code": "BudgetLimitExceeded", "classification": "billing_state", "cooldownSeconds": 3600},
    {"status": 429, "code": "PrepaidBillOverdue", "classification": "billing_state", "cooldownSeconds": 3600},
    {"status": 429, "code": "PostpaidBillOverdue", "classification": "billing_state", "cooldownSeconds": 3600},
    {"status": 400, "code": "Arrearage", "classification": "billing_state", "cooldownSeconds": 3600},
    {"status": 401, "code": "InvalidApiKey", "classification": "invalid_endpoint_pairing", "cooldownSeconds": null},
    {"status": 403, "code": "AccessDenied.Unpurchased", "classification": "model_not_eligible", "cooldownSeconds": null},
    {"status": 403, "code": "Endpoint.AccessDenied", "classification": "model_not_eligible", "cooldownSeconds": null},
    {"status": 403, "code": "AccessDenied", "classification": "model_not_eligible", "cooldownSeconds": null},
    {"status": 403, "code": "AllocationQuota.FreeTierOnly", "classification": "billing_state", "cooldownSeconds": 3600},
    {"status": 404, "code": "model_not_found", "classification": "model_not_found", "cooldownSeconds": null},
    {"status": 404, "code": "model_not_supported", "classification": "model_not_found", "cooldownSeconds": null},
    {"status": 503, "code": "ModelUnavailable", "classification": "unavailable", "cooldownSeconds": 60},
    {"status": 429, "message": "token-plan quota has been exhausted", "classification": "credits_exhausted", "cooldownSeconds": null},
    {"status": 429, "message": "allocated quota exceeded", "classification": "rate_limit", "cooldownSeconds": 60},
    {"status": 401, "message": "invalid access token or token expired", "classification": "invalid_endpoint_pairing", "cooldownSeconds": null}
  ]
}
```

- [ ] **Step 2: Write the schema** (`packages/agent-routing/runtime/model_routing/resources/schemas/model-studio.schema.json`)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://raw.githubusercontent.com/Buckeyes22/pitwall/main/packages/agent-routing/runtime/model_routing/resources/schemas/model-studio.schema.json",
  "title": "Alibaba Cloud Model Studio catalog",
  "type": "object",
  "additionalProperties": false,
  "required": ["schemaVersion", "snapshot", "sources", "paths", "openApi", "plans", "regions", "models", "errors"],
  "properties": {
    "schemaVersion": {"const": 1},
    "snapshot": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"},
    "sources": {"$ref": "#/$defs/sources"},
    "paths": {
      "type": "object", "additionalProperties": false, "required": ["openai", "anthropic", "native"],
      "properties": {"openai": {"type": "string"}, "anthropic": {"type": "string"}, "native": {"type": "string"}}
    },
    "openApi": {
      "type": "object", "additionalProperties": false, "required": ["host", "version", "sources"],
      "properties": {"host": {"type": "string"}, "version": {"type": "string"}, "sources": {"$ref": "#/$defs/sources"}}
    },
    "plans": {"type": "object", "additionalProperties": {"$ref": "#/$defs/plan"}},
    "regions": {"type": "object", "additionalProperties": {"$ref": "#/$defs/region"}},
    "models": {"type": "object", "additionalProperties": {"$ref": "#/$defs/model"}},
    "errors": {"type": "array", "items": {"$ref": "#/$defs/error"}}
  },
  "$defs": {
    "sources": {"type": "array", "minItems": 1, "items": {"type": "string", "pattern": "^[a-z0-9./-]+$"}},
    "price": {"type": "string", "pattern": "^\\d+(\\.\\d+)?$"},
    "optionalPrice": {"anyOf": [{"$ref": "#/$defs/price"}, {"type": "null"}]},
    "positiveOrNull": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]},
    "plan": {
      "type": "object", "additionalProperties": false,
      "required": ["family", "edition", "regions", "keyPrefix", "unit", "cycleDays", "exhaustion", "baseUrls", "tiers", "sources"],
      "properties": {
        "family": {"enum": ["token-plan", "pay-as-you-go"]},
        "edition": {"enum": ["personal", "team", null]},
        "regions": {"type": "array", "minItems": 1, "items": {"type": "string"}},
        "keyPrefix": {"type": ["string", "null"]},
        "unit": {"enum": ["credits", "usd"]},
        "cycleDays": {"$ref": "#/$defs/positiveOrNull"},
        "exhaustion": {"enum": ["blocking", "billing"]},
        "baseUrls": {
          "anyOf": [
            {"type": "object", "additionalProperties": false, "required": ["openai", "anthropic", "native"],
             "properties": {"openai": {"type": "string", "pattern": "^https://"}, "anthropic": {"type": "string", "pattern": "^https://"}, "native": {"type": "string", "pattern": "^https://"}}},
            {"type": "null"}
          ]
        },
        "tiers": {
          "anyOf": [
            {"type": "object", "additionalProperties": {
              "type": "object", "additionalProperties": false, "required": ["credits", "concurrency"],
              "properties": {
                "credits": {"type": "integer", "minimum": 1},
                "concurrency": {"anyOf": [{"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "integer", "minimum": 1}}, {"type": "null"}]}
              }
            }},
            {"type": "null"}
          ]
        },
        "sources": {"$ref": "#/$defs/sources"}
      }
    },
    "region": {
      "type": "object", "additionalProperties": false, "required": ["workspaceHost", "legacyHost", "priced"],
      "properties": {
        "workspaceHost": {"type": "string", "pattern": "^\\{workspace\\}\\."},
        "legacyHost": {"type": ["string", "null"]},
        "priced": {"type": "boolean"}
      }
    },
    "eligibility": {
      "type": "object", "additionalProperties": false, "required": ["personal", "team", "payAsYouGo"],
      "properties": {"personal": {"type": "boolean"}, "team": {"type": "boolean"}, "payAsYouGo": {"type": "boolean"}}
    },
    "tier": {
      "type": "object", "additionalProperties": false, "required": ["aboveInputTokens", "input", "output", "cachedInput"],
      "properties": {
        "aboveInputTokens": {"type": "integer", "minimum": 1},
        "input": {"$ref": "#/$defs/price"}, "output": {"$ref": "#/$defs/price"}, "cachedInput": {"$ref": "#/$defs/optionalPrice"}
      }
    },
    "prices": {
      "anyOf": [
        {"type": "object", "additionalProperties": false, "required": ["input", "output", "cachedInput"],
         "properties": {
           "input": {"$ref": "#/$defs/price"}, "output": {"$ref": "#/$defs/price"}, "cachedInput": {"$ref": "#/$defs/optionalPrice"},
           "tiers": {"type": "array", "items": {"$ref": "#/$defs/tier"}},
           "note": {"type": "string"}
         }},
        {"type": "null"}
      ]
    },
    "effort": {
      "anyOf": [
        {"type": "object", "additionalProperties": false, "required": ["param", "values", "default", "exclusiveWith"],
         "properties": {
           "param": {"const": "reasoning_effort"},
           "values": {"type": "array", "minItems": 1, "items": {"type": "string"}},
           "default": {"type": "string"},
           "exclusiveWith": {"type": "array", "items": {"type": "string"}}
         }},
        {"type": "null"}
      ]
    },
    "model": {
      "type": "object",
      "required": ["kind", "eligibility", "sources"],
      "properties": {
        "kind": {"enum": ["text", "image", "video", "speech", "realtime"]},
        "eligibility": {"$ref": "#/$defs/eligibility"},
        "sources": {"$ref": "#/$defs/sources"}
      },
      "if": {"properties": {"kind": {"const": "text"}}},
      "then": {
        "additionalProperties": false,
        "required": ["contextTokens", "maxInputTokens", "maxInputTokensThinking", "maxOutputTokens", "maxReasoningTokens", "inputs", "reasoning", "effort", "maxTokensIncludesReasoning", "prices"],
        "properties": {
          "kind": true, "eligibility": true, "sources": true,
          "contextTokens": {"$ref": "#/$defs/positiveOrNull"},
          "maxInputTokens": {"$ref": "#/$defs/positiveOrNull"},
          "maxInputTokensThinking": {"$ref": "#/$defs/positiveOrNull"},
          "maxOutputTokens": {"$ref": "#/$defs/positiveOrNull"},
          "maxReasoningTokens": {"$ref": "#/$defs/positiveOrNull"},
          "inputs": {"type": "array", "minItems": 1, "items": {"enum": ["text", "image", "video", "audio"]}},
          "reasoning": {"enum": ["hybrid-on", "hybrid-off", "always", "none", "router"]},
          "effort": {"$ref": "#/$defs/effort"},
          "maxTokensIncludesReasoning": {"type": "boolean"},
          "prices": {"$ref": "#/$defs/prices"}
        }
      },
      "else": {"additionalProperties": false, "properties": {"kind": true, "eligibility": true, "sources": true}}
    },
    "error": {
      "type": "object", "additionalProperties": false,
      "required": ["status", "classification", "cooldownSeconds"],
      "oneOf": [{"required": ["code"]}, {"required": ["message"]}],
      "properties": {
        "status": {"type": "integer"},
        "code": {"type": "string"},
        "message": {"type": "string"},
        "classification": {"enum": ["rate_limit", "credits_exhausted", "billing_state", "invalid_endpoint_pairing", "model_not_eligible", "model_not_found", "unavailable"]},
        "cooldownSeconds": {"$ref": "#/$defs/positiveOrNull"}
      }
    }
  }
}
```

- [ ] **Step 3: Write the Agent Routing catalog tests** (`packages/agent-routing/tests/test_model_studio_catalog.py`)

```python
"""The committed Model Studio catalog is schema-valid and internally consistent."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from model_routing.resources import read_resource_json  # noqa: E402


class ModelStudioCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = read_resource_json("config/model-studio.json")

    def test_catalog_validates_against_its_schema(self) -> None:
        import jsonschema  # dev dependency, as in tools/validate_json_schemas.py

        schema = read_resource_json("schemas/model-studio.schema.json")
        jsonschema.Draft202012Validator(schema).validate(self.catalog)

    def test_every_plan_region_is_a_known_region(self) -> None:
        for name, plan in self.catalog["plans"].items():
            for region in plan["regions"]:
                self.assertIn(region, self.catalog["regions"], name)

    def test_token_plans_are_singapore_only_with_sk_sp_keys(self) -> None:
        for plan in self.catalog["plans"].values():
            if plan["family"] == "token-plan":
                self.assertEqual(["ap-southeast-1"], plan["regions"])
                self.assertEqual("sk-sp-", plan["keyPrefix"])
                self.assertTrue(plan["tiers"])

    def test_text_models_publish_limits_except_the_router(self) -> None:
        for name, model in self.catalog["models"].items():
            if model["kind"] != "text" or model["reasoning"] == "router":
                continue
            self.assertIsNotNone(model["maxInputTokens"], name)
            self.assertIsNotNone(model["maxOutputTokens"], name)
            self.assertLessEqual(model["maxOutputTokens"], model["contextTokens"], name)

    def test_effort_defaults_are_members_of_their_values(self) -> None:
        for name, model in self.catalog["models"].items():
            effort = model.get("effort")
            if effort:
                self.assertIn(effort["default"], effort["values"], name)

    def test_price_tiers_ascend(self) -> None:
        for name, model in self.catalog["models"].items():
            prices = model.get("prices") or {}
            thresholds = [tier["aboveInputTokens"] for tier in prices.get("tiers", [])]
            self.assertEqual(sorted(thresholds), thresholds, name)

    def test_personal_pro_is_180000_credits_with_up_to_eight_agents(self) -> None:
        pro = self.catalog["plans"]["token-plan-personal"]["tiers"]["pro"]
        self.assertEqual({"credits": 180000, "concurrency": [6, 8]}, pro)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 4: Register the schema and the packaged file**

In `packages/agent-routing/tools/validate_json_schemas.py` `main()` change the count check and add the catalog validation after the model-catalog line:

```python
    if len(schema_paths) != 9:
        raise AssertionError(f"expected nine packaged schemas, found {len(schema_paths)}")
```

```python
    validate("model-studio.schema.json", read_resource_json("config/model-studio.json"))
```

In `packages/agent-routing/tools/release/inspect_artifacts.py` add `"model_routing/resources/config/model-studio.json",` after line 121 and `"model_routing/resources/schemas/model-studio.schema.json",` after line 122 (`routes.schema.json`).

- [ ] **Step 5: Copy the catalog into the broker and write the parity test**

```bash
mkdir -p src/pitwall/providers/model_studio
cp packages/agent-routing/runtime/model_routing/resources/config/model-studio.json src/pitwall/providers/model_studio/catalog.json
printf '"""Alibaba Cloud Model Studio provider."""\n' > src/pitwall/providers/model_studio/__init__.py
```

`tests/test_model_studio_catalog_parity.py`:

```python
"""Agent Routing and the broker ship byte-identical Model Studio catalogs."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_CANONICAL = (
    _ROOT / "packages/agent-routing/runtime/model_routing/resources/config/model-studio.json"
)


def test_broker_catalog_is_byte_identical_to_the_canonical_copy() -> None:
    broker = resources.files("pitwall.providers.model_studio").joinpath("catalog.json")
    assert broker.read_bytes() == _CANONICAL.read_bytes(), (
        "copy packages/agent-routing/runtime/model_routing/resources/config/model-studio.json "
        "to src/pitwall/providers/model_studio/catalog.json"
    )
```

- [ ] **Step 6: Run the tests and tools**

```bash
uv run pytest tests/test_model_studio_catalog_parity.py -q 2>&1 | tail -3
cd packages/agent-routing && .venv/bin/python -m unittest tests.test_model_studio_catalog -v 2>&1 | tail -12
.venv/bin/python tools/validate_json_schemas.py 2>&1 | tail -2; cd ../..
uv build --wheel -o /tmp/pitwall-wheel >/dev/null && unzip -l /tmp/pitwall-wheel/pitwall-*.whl | grep model_studio/catalog.json
```

Expected: parity `1 passed`; catalog tests `OK` (7 tests); validator prints `all schemas and representative runtime documents are valid`; the wheel listing shows `pitwall/providers/model_studio/catalog.json`.

- [ ] **Step 7: Commit**

```bash
git add packages/agent-routing/runtime/model_routing/resources packages/agent-routing/tools packages/agent-routing/tests/test_model_studio_catalog.py src/pitwall/providers/model_studio tests/test_model_studio_catalog_parity.py
git commit -m "feat(model-studio): shared catalog with schema and drift test"
```

---

### Task 3: Agent Routing `model_studio` module (catalog rules)

**Files:**
- Create: `packages/agent-routing/runtime/model_routing/model_studio.py`
- Test: `packages/agent-routing/tests/test_model_studio.py`

**Interfaces:**
- Consumes: `read_resource_json("config/model-studio.json")`.
- Produces (used by Tasks 4–9):
  - `KIND = "model-studio"`, `AUTOMATION_ENV = "MODEL_STUDIO_TOKEN_PLAN_AUTOMATION"`, `AUTOMATION_ACCEPT = "accept"`, `EXHAUSTED_MESSAGE = "token-plan quota has been exhausted"`
  - `class ModelStudioError(ValueError)` with `.code: str`
  - `load_catalog() -> Mapping[str, Any]`
  - `plan_record(plan: str) -> Mapping[str, Any]`, `is_token_plan(plan: str) -> bool`
  - `validate_endpoint(raw: Mapping[str, Any], field: str) -> dict[str, Any]` (normalized, includes derived `baseUrl`)
  - `base_url(endpoint: Mapping[str, Any], protocol: str | None = None) -> str` (`protocol` may be `"native"`)
  - `check_key(plan: str, key: str) -> None`
  - `model_record(model: str) -> Mapping[str, Any]`, `check_model(plan: str, model: str) -> Mapping[str, Any]`
  - `route_limits(model: str) -> dict[str, int] | None`
  - `effort_values(model: str) -> tuple[str, ...]`
  - `automation_accepted(endpoint: Mapping[str, Any]) -> bool`
  - `classify_error(status: int, body: str) -> tuple[str, int | None] | None`
  - `credits_window(renews_on: str, now: datetime) -> tuple[datetime, datetime]`

- [ ] **Step 1: Write the failing tests** (`packages/agent-routing/tests/test_model_studio.py`)

```python
"""Model Studio catalog rules: pairing, regions, eligibility, derivation, classification."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from model_routing import model_studio as ms  # noqa: E402

TOKEN = "https://token-plan.ap-southeast-1.maas.aliyuncs.com"


def personal(**extra: object) -> dict[str, object]:
    return {
        "kind": "model-studio",
        "plan": "token-plan-personal",
        "tier": "pro",
        "apiKeyEnv": "MODEL_STUDIO_API_KEY",
        **extra,
    }


class EndpointValidationTests(unittest.TestCase):
    def test_token_plan_defaults_region_protocol_and_concurrency(self) -> None:
        normalized = ms.validate_endpoint(personal(), "endpoints.ms")
        self.assertEqual("ap-southeast-1", normalized["region"])
        self.assertEqual("openai", normalized["protocol"])
        self.assertEqual(8, normalized["concurrency"])
        self.assertEqual(f"{TOKEN}/compatible-mode/v1", normalized["baseUrl"])

    def test_anthropic_protocol_derives_the_anthropic_base(self) -> None:
        normalized = ms.validate_endpoint(personal(protocol="anthropic"), "endpoints.ms")
        self.assertEqual(f"{TOKEN}/apps/anthropic", normalized["baseUrl"])

    def test_token_plan_outside_singapore_is_refused(self) -> None:
        with self.assertRaises(ms.ModelStudioError) as caught:
            ms.validate_endpoint(personal(region="us-east-1"), "endpoints.ms")
        self.assertEqual("region_not_allowed", caught.exception.code)

    def test_token_plan_requires_a_tier(self) -> None:
        raw = personal()
        del raw["tier"]
        with self.assertRaises(ms.ModelStudioError):
            ms.validate_endpoint(raw, "endpoints.ms")

    def test_pay_as_you_go_requires_a_known_region(self) -> None:
        with self.assertRaises(ms.ModelStudioError) as caught:
            ms.validate_endpoint(
                {"kind": "model-studio", "plan": "pay-as-you-go", "apiKeyEnv": "K"}, "e"
            )
        self.assertEqual("unknown_region", caught.exception.code)

    def test_pay_as_you_go_prefers_the_workspace_host(self) -> None:
        normalized = ms.validate_endpoint(
            {
                "kind": "model-studio",
                "plan": "pay-as-you-go",
                "region": "ap-southeast-1",
                "workspace": "ws-1a2b",
                "apiKeyEnv": "K",
            },
            "e",
        )
        self.assertEqual(
            "https://ws-1a2b.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
            normalized["baseUrl"],
        )
        self.assertNotIn("concurrency", normalized)

    def test_region_without_a_shared_host_requires_a_workspace(self) -> None:
        with self.assertRaises(ms.ModelStudioError) as caught:
            ms.validate_endpoint(
                {
                    "kind": "model-studio",
                    "plan": "pay-as-you-go",
                    "region": "eu-central-1",
                    "apiKeyEnv": "K",
                },
                "e",
            )
        self.assertEqual("workspace_required", caught.exception.code)

    def test_a_stored_base_url_must_equal_the_derived_one(self) -> None:
        ms.validate_endpoint(personal(baseUrl=f"{TOKEN}/compatible-mode/v1"), "e")
        with self.assertRaises(ms.ModelStudioError) as caught:
            ms.validate_endpoint(
                personal(baseUrl="https://dashscope-intl.aliyuncs.com/compatible-mode/v1"), "e"
            )
        self.assertEqual("invalid_endpoint_pairing", caught.exception.code)

    def test_renews_on_is_a_date_and_only_for_token_plan(self) -> None:
        self.assertEqual(
            "2026-09-12", ms.validate_endpoint(personal(renewsOn="2026-09-12"), "e")["renewsOn"]
        )
        with self.assertRaises(ms.ModelStudioError):
            ms.validate_endpoint(personal(renewsOn=12), "e")

    def test_automation_acceptance_is_recorded_only_as_accept(self) -> None:
        self.assertTrue(
            ms.automation_accepted(
                ms.validate_endpoint(personal(tokenPlanAutomation="accept"), "e")
            )
        )
        self.assertFalse(ms.automation_accepted(ms.validate_endpoint(personal(), "e")))
        with self.assertRaises(ms.ModelStudioError):
            ms.validate_endpoint(personal(tokenPlanAutomation="yes"), "e")


class RuleTests(unittest.TestCase):
    def test_token_plan_key_pairs_only_with_token_plan(self) -> None:
        ms.check_key("token-plan-personal", "sk-sp-example")
        for plan, key in (
            ("token-plan-personal", "sk-example"),
            ("pay-as-you-go", "sk-sp-example"),
        ):
            with self.assertRaises(ms.ModelStudioError) as caught:
                ms.check_key(plan, key)
            self.assertEqual("key_plan_mismatch", caught.exception.code)
            self.assertNotIn("example", str(caught.exception))

    def test_team_only_model_is_refused_on_personal(self) -> None:
        with self.assertRaises(ms.ModelStudioError) as caught:
            ms.check_model("token-plan-personal", "kimi-k2.7-code")
        self.assertEqual("model_not_eligible", caught.exception.code)
        ms.check_model("token-plan-team", "kimi-k2.7-code")

    def test_unknown_and_non_text_models_are_refused(self) -> None:
        with self.assertRaises(ms.ModelStudioError) as caught:
            ms.check_model("token-plan-personal", "qwen9-imaginary")
        self.assertEqual("model_not_in_catalog", caught.exception.code)
        with self.assertRaises(ms.ModelStudioError) as caught:
            ms.check_model("token-plan-personal", "wan2.7-image")
        self.assertEqual("model_not_text", caught.exception.code)

    def test_router_model_is_token_plan_only(self) -> None:
        ms.check_model("token-plan-personal", "auto")
        with self.assertRaises(ms.ModelStudioError):
            ms.check_model("pay-as-you-go", "auto")

    def test_limits_and_effort_come_from_the_catalog(self) -> None:
        self.assertEqual({"context": 991808, "output": 131072}, ms.route_limits("qwen3.8-flash"))
        self.assertIsNone(ms.route_limits("auto"))
        self.assertEqual(("xhigh", "medium", "low"), ms.effort_values("qwen3.8-max"))
        self.assertEqual((), ms.effort_values("glm-5.2"))


class ClassificationTests(unittest.TestCase):
    def test_code_wins_before_message(self) -> None:
        body = '{"code": "Throttling.AllocationQuota", "message": "Allocated quota exceeded"}'
        self.assertEqual(("rate_limit", 60), ms.classify_error(429, body))

    def test_openai_shaped_token_plan_exhaustion(self) -> None:
        body = '{"error": {"code": "insufficient_quota", "message": "Your token-plan quota has been exhausted."}}'
        self.assertEqual(("credits_exhausted", None), ms.classify_error(429, body))

    def test_openai_shaped_allocated_quota_is_per_minute(self) -> None:
        body = '{"error": {"code": "insufficient_quota", "message": "Allocated quota exceeded, please increase your quota limit."}}'
        self.assertEqual(("rate_limit", 60), ms.classify_error(429, body))

    def test_billing_states_and_unknown_bodies(self) -> None:
        self.assertEqual(("billing_state", 3600), ms.classify_error(400, '{"code": "Arrearage"}'))
        self.assertIsNone(ms.classify_error(429, "not json at all"))


class RenewalTests(unittest.TestCase):
    def test_window_steps_in_thirty_day_cycles_from_the_anchor(self) -> None:
        now = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)
        start, reset = ms.credits_window("2026-09-12", now)
        self.assertEqual(datetime(2026, 11, 11, tzinfo=timezone.utc), start)
        self.assertEqual(datetime(2026, 12, 11, tzinfo=timezone.utc), reset)

    def test_future_anchor_still_yields_the_current_window(self) -> None:
        now = datetime(2026, 9, 1, tzinfo=timezone.utc)
        start, reset = ms.credits_window("2026-09-12", now)
        self.assertEqual(datetime(2026, 8, 13, tzinfo=timezone.utc), start)
        self.assertEqual(datetime(2026, 9, 12, tzinfo=timezone.utc), reset)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd packages/agent-routing && .venv/bin/python -m unittest tests.test_model_studio 2>&1 | tail -5`
Expected: `ModuleNotFoundError: No module named 'model_routing.model_studio'`.

- [ ] **Step 3: Implement** (`packages/agent-routing/runtime/model_routing/model_studio.py`)

```python
"""Alibaba Cloud Model Studio: catalog lookups and configuration rules (stdlib only)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
import json
import re
from typing import Any, Mapping

from .resources import read_resource_json

CATALOG_RESOURCE = "config/model-studio.json"
KIND = "model-studio"
AUTOMATION_ENV = "MODEL_STUDIO_TOKEN_PLAN_AUTOMATION"
AUTOMATION_ACCEPT = "accept"
EXHAUSTED_MESSAGE = "token-plan quota has been exhausted"
PROTOCOLS = ("openai", "anthropic")
ENDPOINT_FIELDS = frozenset(
    {
        "kind",
        "plan",
        "region",
        "workspace",
        "protocol",
        "tier",
        "concurrency",
        "renewsOn",
        "apiKeyEnv",
        "baseUrl",
        "tokenPlanAutomation",
    }
)
_WORKSPACE = re.compile(r"[A-Za-z0-9-]{1,64}")
_ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]*")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class ModelStudioError(ValueError):
    """A named Model Studio configuration refusal; messages never carry key values."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@lru_cache(maxsize=1)
def load_catalog() -> Mapping[str, Any]:
    catalog: Mapping[str, Any] = read_resource_json(CATALOG_RESOURCE)
    return catalog


def plan_record(plan: str) -> Mapping[str, Any]:
    plans = load_catalog()["plans"]
    if plan not in plans:
        raise ModelStudioError("unknown_plan", f"plan must be one of {', '.join(sorted(plans))}")
    record: Mapping[str, Any] = plans[plan]
    return record


def is_token_plan(plan: str) -> bool:
    return plan_record(plan)["family"] == "token-plan"


def validate_endpoint(raw: Mapping[str, Any], field: str) -> dict[str, Any]:
    unknown = set(raw) - ENDPOINT_FIELDS
    if unknown:
        raise ModelStudioError(
            "invalid_endpoint", f"{field} has unknown fields: {', '.join(sorted(unknown))}"
        )
    plan = raw.get("plan")
    if not isinstance(plan, str):
        raise ModelStudioError("unknown_plan", f"{field}.plan is required")
    record = plan_record(plan)
    token_plan = record["family"] == "token-plan"
    region = raw.get("region", record["regions"][0] if token_plan else None)
    if region not in record["regions"]:
        if token_plan:
            raise ModelStudioError(
                "region_not_allowed",
                f"{field}: Token Plan is served only from {record['regions'][0]}",
            )
        raise ModelStudioError(
            "unknown_region", f"{field}.region must be one of {', '.join(record['regions'])}"
        )
    protocol = raw.get("protocol", "openai")
    if protocol not in PROTOCOLS:
        raise ModelStudioError("invalid_endpoint", f"{field}.protocol must be openai or anthropic")
    key_env = raw.get("apiKeyEnv")
    if not isinstance(key_env, str) or _ENV_NAME.fullmatch(key_env) is None:
        raise ModelStudioError(
            "invalid_endpoint", f"{field}.apiKeyEnv must name an environment variable"
        )
    normalized: dict[str, Any] = {
        "kind": KIND,
        "plan": plan,
        "region": region,
        "protocol": protocol,
        "apiKeyEnv": key_env,
    }
    workspace = raw.get("workspace")
    if workspace is not None:
        if token_plan:
            raise ModelStudioError(
                "invalid_endpoint", f"{field}.workspace applies only to pay-as-you-go"
            )
        if not isinstance(workspace, str) or _WORKSPACE.fullmatch(workspace) is None:
            raise ModelStudioError("invalid_endpoint", f"{field}.workspace must be a workspace id")
        normalized["workspace"] = workspace
    tiers: Mapping[str, Any] = record.get("tiers") or {}
    tier = raw.get("tier")
    if tier is not None:
        if tier not in tiers:
            allowed = ", ".join(tiers) if tiers else "none (tiers apply only to Token Plan)"
            raise ModelStudioError("invalid_endpoint", f"{field}.tier must be one of {allowed}")
        normalized["tier"] = tier
    elif token_plan:
        raise ModelStudioError("invalid_endpoint", f"{field}.tier is required for {plan}")
    concurrency = raw.get("concurrency")
    if concurrency is None and tier is not None and tiers[tier].get("concurrency"):
        concurrency = tiers[tier]["concurrency"][1]
    if concurrency is not None:
        if isinstance(concurrency, bool) or not isinstance(concurrency, int) or concurrency < 1:
            raise ModelStudioError(
                "invalid_endpoint", f"{field}.concurrency must be a positive integer"
            )
        normalized["concurrency"] = concurrency
    renews_on = raw.get("renewsOn")
    if renews_on is not None:
        if not token_plan:
            raise ModelStudioError(
                "invalid_endpoint", f"{field}.renewsOn applies only to Token Plan"
            )
        if not isinstance(renews_on, str) or _DATE.fullmatch(renews_on) is None:
            raise ModelStudioError(
                "invalid_endpoint", f"{field}.renewsOn must be a YYYY-MM-DD date"
            )
        date.fromisoformat(renews_on)
        normalized["renewsOn"] = renews_on
    automation = raw.get("tokenPlanAutomation")
    if automation is not None:
        if not token_plan or automation != AUTOMATION_ACCEPT:
            raise ModelStudioError(
                "invalid_endpoint",
                f"{field}.tokenPlanAutomation must be 'accept' and applies only to Token Plan",
            )
        normalized["tokenPlanAutomation"] = automation
    derived = base_url(normalized)
    if "baseUrl" in raw and raw["baseUrl"] != derived:
        raise ModelStudioError(
            "invalid_endpoint_pairing",
            f"{field}.baseUrl is derived from plan, region, workspace, and protocol; remove it",
        )
    normalized["baseUrl"] = derived
    return normalized


def base_url(endpoint: Mapping[str, Any], protocol: str | None = None) -> str:
    catalog = load_catalog()
    record = plan_record(str(endpoint["plan"]))
    chosen = protocol or str(endpoint.get("protocol", "openai"))
    if record["family"] == "token-plan":
        return str(record["baseUrls"][chosen])
    region = catalog["regions"][str(endpoint["region"])]
    workspace = endpoint.get("workspace")
    if workspace:
        host = str(region["workspaceHost"]).format(workspace=workspace)
    elif region.get("legacyHost"):
        host = str(region["legacyHost"])
    else:
        raise ModelStudioError(
            "workspace_required",
            f"region {endpoint['region']} has no shared DashScope host; set a workspace id",
        )
    return f"https://{host}{catalog['paths'][chosen]}"


def check_key(plan: str, key: str) -> None:
    prefix = str(load_catalog()["plans"]["token-plan-personal"]["keyPrefix"])
    token_key = key.startswith(prefix)
    if is_token_plan(plan) and not token_key:
        raise ModelStudioError(
            "key_plan_mismatch",
            f"plan {plan} needs a Token Plan key (prefix {prefix}); another key would be billed pay-as-you-go",
        )
    if token_key and not is_token_plan(plan):
        raise ModelStudioError(
            "key_plan_mismatch",
            f"a Token Plan key (prefix {prefix}) works only with a Token Plan plan",
        )


def model_record(model: str) -> Mapping[str, Any]:
    models = load_catalog()["models"]
    if model not in models:
        raise ModelStudioError(
            "model_not_in_catalog", f"model {model!r} is not in the Model Studio catalog"
        )
    record: Mapping[str, Any] = models[model]
    return record


def check_model(plan: str, model: str) -> Mapping[str, Any]:
    record = model_record(model)
    edition = plan_record(plan)["edition"]
    eligible = (
        record["eligibility"]["payAsYouGo"] if edition is None else record["eligibility"][edition]
    )
    if not eligible:
        raise ModelStudioError("model_not_eligible", f"model {model!r} is not included in {plan}")
    if record["kind"] != "text":
        raise ModelStudioError(
            "model_not_text",
            f"model {model!r} is a {record['kind']} model; only text models dispatch",
        )
    return record


def route_limits(model: str) -> dict[str, int] | None:
    record = model_record(model)
    if record.get("maxInputTokens") is None or record.get("maxOutputTokens") is None:
        return None
    return {"context": int(record["maxInputTokens"]), "output": int(record["maxOutputTokens"])}


def effort_values(model: str) -> tuple[str, ...]:
    effort = model_record(model).get("effort")
    return tuple(str(value) for value in effort["values"]) if effort else ()


def automation_accepted(endpoint: Mapping[str, Any]) -> bool:
    return endpoint.get("tokenPlanAutomation") == AUTOMATION_ACCEPT


def _error_code_and_message(body: str) -> tuple[str | None, str]:
    try:
        payload = json.loads(body)
    except ValueError:
        return None, body
    if not isinstance(payload, dict):
        return None, body
    nested = payload.get("error")
    source = nested if isinstance(nested, dict) else payload
    code = source.get("code")
    message = source.get("message")
    return (str(code) if code else None), (str(message) if message else body)


def classify_error(status: int, body: str) -> tuple[str, int | None] | None:
    code, message = _error_code_and_message(body)
    entries = load_catalog()["errors"]
    for entry in entries:
        if "code" in entry and entry["status"] == status and entry["code"] == code:
            return str(entry["classification"]), entry["cooldownSeconds"]
    lowered = message.lower()
    for entry in entries:
        if "message" in entry and entry["status"] == status and entry["message"] in lowered:
            return str(entry["classification"]), entry["cooldownSeconds"]
    return None


def credits_window(renews_on: str, now: datetime) -> tuple[datetime, datetime]:
    anchor = datetime.combine(
        date.fromisoformat(renews_on), datetime.min.time(), tzinfo=timezone.utc
    )
    cycle = timedelta(days=30)
    cycles = (now - anchor) // cycle
    start = anchor + cycles * cycle
    return start, start + cycle
```

- [ ] **Step 4: Run tests**

Run: `cd packages/agent-routing && .venv/bin/python -m unittest tests.test_model_studio -v 2>&1 | tail -6`
Expected: `Ran 22 tests` … `OK`.

- [ ] **Step 5: Lint and type-check the new module**

Run: `cd packages/agent-routing && .venv/bin/ruff check runtime/model_routing/model_studio.py tests/test_model_studio.py && .venv/bin/mypy --python-version 3.14 runtime/model_routing/model_studio.py 2>&1 | tail -2`
Expected: `All checks passed!` and `Success: no issues found in 1 source file`.

- [ ] **Step 6: Commit**

```bash
git add packages/agent-routing/runtime/model_routing/model_studio.py packages/agent-routing/tests/test_model_studio.py
git commit -m "feat(agent-routing): Model Studio catalog rules and endpoint derivation"
```

---

### Task 4: The `model-studio` endpoint kind in `routes.json`

**Files:**
- Modify: `packages/agent-routing/runtime/model_routing/routes.py` — `EndpointReference` (line 159) value typing, `_validate_endpoint` (line 192), `validate_routes` (endpoint loop at line 282, entry loop before `normalized_models[name] = normalized` at line 440); add `_validate_model_studio_route`.
- Modify: `packages/agent-routing/runtime/model_routing/resources/schemas/routes.schema.json` (`$defs.endpoint`)
- Create: `packages/agent-routing/tests/fixtures/routes/positive/model-studio.json`, `packages/agent-routing/tests/fixtures/routes/negative/model-studio-token-plan-region.json`
- Test: `packages/agent-routing/tests/test_routes_model_studio.py`

**Interfaces:**
- Consumes: Task 3 `validate_endpoint`, `check_model`, `effort_values`, `ModelStudioError`, `KIND`.
- Produces: normalized endpoints of kind `model-studio` (with derived `baseUrl`) in `validate_routes` output; `resolved_endpoint(entry)` returns them unchanged.

- [ ] **Step 1: Write the failing tests** (`packages/agent-routing/tests/test_routes_model_studio.py`)

```python
"""routes.json accepts Model Studio endpoints and enforces the catalog on their routes."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from model_routing import routes  # noqa: E402
from model_routing.registry import load_registry  # noqa: E402

REGISTRY = load_registry()
BASE = "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"


def document(**route: object) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "endpoints": {
            "ms": {
                "kind": "model-studio",
                "plan": "token-plan-personal",
                "tier": "pro",
                "apiKeyEnv": "MODEL_STUDIO_API_KEY",
            }
        },
        "models": {
            "flash": {"model": "qwen3.8-flash", "endpoint": "ms", "harness": "opencode", **route}
        },
    }


class ModelStudioRoutesTests(unittest.TestCase):
    def test_endpoint_normalizes_with_derived_base_url(self) -> None:
        data = routes.validate_routes(document(), registry=REGISTRY)
        endpoint = routes.resolved_endpoint(data["models"]["flash"])
        assert endpoint is not None
        self.assertEqual(BASE, endpoint["baseUrl"])
        self.assertEqual("model-studio", endpoint["kind"])

    def test_saved_endpoint_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {"SUBAGENT_MODEL_ROUTING_ROUTES": os.path.join(tmp, "routes.json")}
            routes.save_routes(env, document(), registry=REGISTRY)
            first = Path(env["SUBAGENT_MODEL_ROUTING_ROUTES"]).read_text(encoding="utf-8")
            routes.save_routes(env, routes.load_routes(env, registry=REGISTRY), registry=REGISTRY)
            self.assertEqual(
                first, Path(env["SUBAGENT_MODEL_ROUTING_ROUTES"]).read_text(encoding="utf-8")
            )
            self.assertIn(BASE, first)

    def test_ineligible_model_is_refused(self) -> None:
        doc = document()
        doc["models"]["flash"]["model"] = "kimi-k2.7-code"  # type: ignore[index]
        with self.assertRaisesRegex(routes.RoutesError, "model_not_eligible"):
            routes.validate_routes(doc, registry=REGISTRY)

    def test_effort_uses_the_model_vocabulary(self) -> None:
        routes.validate_routes(document(effort="medium"), registry=REGISTRY)
        with self.assertRaisesRegex(routes.RoutesError, "xhigh, medium, low"):
            routes.validate_routes(document(effort="high"), registry=REGISTRY)

    def test_anthropic_protocol_is_opencode_only_and_takes_no_effort(self) -> None:
        doc = document()
        doc["endpoints"]["ms"]["protocol"] = "anthropic"  # type: ignore[index]
        routes.validate_routes(doc, registry=REGISTRY)
        doc["models"]["flash"]["harness"] = "pi"  # type: ignore[index]
        with self.assertRaisesRegex(routes.RoutesError, "anthropic"):
            routes.validate_routes(doc, registry=REGISTRY)
        doc["models"]["flash"]["harness"] = "opencode"  # type: ignore[index]
        doc["models"]["flash"]["effort"] = "low"  # type: ignore[index]
        with self.assertRaisesRegex(routes.RoutesError, "effort"):
            routes.validate_routes(doc, registry=REGISTRY)

    def test_plain_endpoints_are_unchanged(self) -> None:
        doc = {
            "schemaVersion": 1,
            "endpoints": {"local": {"baseUrl": "http://127.0.0.1:8000/v1"}},
            "models": {},
        }
        self.assertEqual(
            {"baseUrl": "http://127.0.0.1:8000/v1"},
            routes.validate_routes(doc, registry=REGISTRY)["endpoints"]["local"],
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd packages/agent-routing && .venv/bin/python -m unittest tests.test_routes_model_studio 2>&1 | tail -4`
Expected: FAIL, `routes.RoutesError: endpoints.ms must contain baseUrl and optionally apiKeyEnv`.

- [ ] **Step 3: Implement in `routes.py`**

Add the import beside the other package imports (after line 19):

```python
from . import model_studio
```

Change `EndpointReference` value types (lines 162–178) from `Mapping[str, str]` to `Mapping[str, Any]` (the `_resolved` annotation, `__new__` parameter, and `resolved` property), and replace `_validate_endpoint` (lines 192–210):

```python
def _validate_endpoint(endpoint: Any, field: str) -> dict[str, Any]:
    if isinstance(endpoint, dict) and endpoint.get("kind") == model_studio.KIND:
        try:
            return model_studio.validate_endpoint(endpoint, field)
        except model_studio.ModelStudioError as exc:
            raise RoutesError(str(exc)) from exc
    _require(
        isinstance(endpoint, dict)
        and set(endpoint) <= {"baseUrl", "apiKeyEnv"}
        and "baseUrl" in endpoint,
        f"{field} must contain baseUrl and optionally apiKeyEnv, or kind: model-studio",
    )
    base_url = endpoint["baseUrl"]
    _require(
        isinstance(base_url, str) and re.fullmatch(r"https?://\S+", base_url) is not None,
        f"{field}.baseUrl must be an http(s) URL",
    )
    normalized: dict[str, Any] = {"baseUrl": base_url}
    if "apiKeyEnv" in endpoint:
        key_env = endpoint["apiKeyEnv"]
        _require(
            isinstance(key_env, str) and ENV_NAME.fullmatch(key_env) is not None,
            f"{field}.apiKeyEnv must name an environment variable",
        )
        normalized["apiKeyEnv"] = key_env
    return normalized


def _validate_model_studio_route(
    entry: Mapping[str, Any], endpoint: Mapping[str, Any], field: str, *, harness: str
) -> None:
    model = str(entry["model"])
    try:
        model_studio.check_model(str(endpoint["plan"]), model)
    except model_studio.ModelStudioError as exc:
        raise RoutesError(f"{field}: {exc}") from exc
    if endpoint.get("protocol") == "anthropic":
        _require(
            harness == "opencode",
            f"{field}: protocol anthropic is supported only by the opencode harness",
        )
        _require(
            "effort" not in entry,
            f"{field}.effort is not supported on the anthropic protocol; use protocol openai",
        )
    if "effort" in entry:
        values = model_studio.effort_values(model)
        _require(bool(values), f"{field}.effort: model {model!r} has no effort control")
        _require(
            entry["effort"] in values,
            f"{field}.effort must be one of {', '.join(values)} for {model}",
        )
```

In `validate_routes`, change `normalized_endpoints: dict[str, dict[str, str]] = {}` (line 282) to `dict[str, dict[str, Any]]`, and insert immediately before `normalized_models[name] = normalized` (line 440):

```python
endpoint_object = resolved_endpoint(normalized)
if endpoint_object is not None and endpoint_object.get("kind") == model_studio.KIND:
    _validate_model_studio_route(
        normalized, endpoint_object, field, harness=entry_harness(normalized, defaults, registry)
    )
```

Note: the existing `validate_effort` call (line 435) still runs first and checks the harness's own vocabulary; the catalog check narrows it further.

- [ ] **Step 4: Update the schema** — in `routes.schema.json` replace `$defs.endpoint` with:

```json
"endpoint": {
  "oneOf": [
    {
      "type": "object",
      "additionalProperties": false,
      "required": ["baseUrl"],
      "properties": {
        "baseUrl": {"type": "string", "pattern": "^https?://\\S+$"},
        "apiKeyEnv": {"type": "string", "pattern": "^[A-Z][A-Z0-9_]*$"}
      }
    },
    {
      "type": "object",
      "additionalProperties": false,
      "required": ["kind", "plan", "apiKeyEnv"],
      "properties": {
        "kind": {"const": "model-studio"},
        "plan": {"enum": ["token-plan-personal", "token-plan-team", "pay-as-you-go"]},
        "region": {"enum": ["ap-southeast-1", "cn-beijing", "us-east-1", "eu-central-1", "ap-northeast-1", "cn-hongkong"]},
        "workspace": {"type": "string", "pattern": "^[A-Za-z0-9-]{1,64}$"},
        "protocol": {"enum": ["openai", "anthropic"]},
        "tier": {"enum": ["lite", "essential", "standard", "pro", "advanced", "premium"]},
        "concurrency": {"type": "integer", "minimum": 1},
        "renewsOn": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"},
        "tokenPlanAutomation": {"const": "accept"},
        "apiKeyEnv": {"type": "string", "pattern": "^[A-Z][A-Z0-9_]*$"},
        "baseUrl": {"type": "string", "pattern": "^https://\\S+$"}
      }
    }
  ]
}
```

Positive fixture `tests/fixtures/routes/positive/model-studio.json`:

```json
{
  "schemaVersion": 1,
  "endpoints": {
    "model-studio": {"kind": "model-studio", "plan": "token-plan-personal", "tier": "pro", "apiKeyEnv": "MODEL_STUDIO_API_KEY", "renewsOn": "2026-09-12"}
  },
  "models": {
    "qwen-flash": {"model": "qwen3.8-flash", "endpoint": "model-studio", "harness": "opencode", "limits": {"context": 991808, "output": 131072}}
  }
}
```

Negative fixture `tests/fixtures/routes/negative/model-studio-token-plan-region.json` (schema-invalid because `baseUrl` must be `https`):

```json
{
  "schemaVersion": 1,
  "endpoints": {"ms": {"kind": "model-studio", "plan": "token-plan-personal", "apiKeyEnv": "K", "baseUrl": "http://example.invalid/v1"}},
  "models": {}
}
```

- [ ] **Step 5: Run tests and the schema tool**

```bash
cd packages/agent-routing
.venv/bin/python -m unittest tests.test_routes_model_studio tests.test_routes -v 2>&1 | tail -4
.venv/bin/python tools/validate_json_schemas.py 2>&1 | tail -1
.venv/bin/mypy --python-version 3.14 runtime/model_routing 2>&1 | tail -1
```

Expected: `OK`; `all schemas and representative runtime documents are valid`; `Success: no issues found`.

- [ ] **Step 6: Commit**

```bash
git add packages/agent-routing/runtime/model_routing/routes.py packages/agent-routing/runtime/model_routing/resources/schemas/routes.schema.json packages/agent-routing/tests/test_routes_model_studio.py packages/agent-routing/tests/fixtures/routes
git commit -m "feat(agent-routing): model-studio endpoint kind with catalog-checked routes"
```

---

### Task 5: CLI — `routes add-model-studio-endpoint` and catalog limits on `routes add`

**Files:**
- Modify: `packages/agent-routing/runtime/model_routing/cli.py` — `_routes_add` (line 705; before the `add_route` call at line 825), new `_routes_add_model_studio_endpoint`, parser (lines 1327–1344) and the routes command dispatcher (search `args.routes_command == "add"`).
- Test: `packages/agent-routing/tests/test_cli_model_studio.py`

**Interfaces:**
- Consumes: Task 3 `load_catalog`, `route_limits`, `AUTOMATION_ENV`, `AUTOMATION_ACCEPT`, `KIND`; `_routes_context()`, `save_routes`.
- Produces: CLI `pitwall-agent-routing routes add-model-studio-endpoint NAME --plan PLAN [--api-key-env NAME] [--region R] [--model-studio-workspace ID] [--protocol openai|anthropic] [--tier T] [--concurrency N] [--renews-on YYYY-MM-DD] [--accept-token-plan-automation]`; `routes add NAME --model M --endpoint E` fills limits and pins the harness when E is Model Studio.

- [ ] **Step 1: Write the failing tests** (`packages/agent-routing/tests/test_cli_model_studio.py`)

```python
"""The CLI creates Model Studio endpoints and fills route limits from the catalog."""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from model_routing import cli  # noqa: E402


class ModelStudioCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.routes = Path(self.tmp.name) / "routes.json"
        self.env = {
            "HOME": self.tmp.name,
            "SUBAGENT_MODEL_ROUTING_ROUTES": str(self.routes),
            "PATH": os.environ.get("PATH", ""),
        }

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_cli(self, *argv: str, extra_env: dict[str, str] | None = None) -> tuple[int, str]:
        err = io.StringIO()
        with (
            mock.patch.dict(os.environ, {**self.env, **(extra_env or {})}, clear=True),
            contextlib.redirect_stderr(err),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            code = cli.main(list(argv))
        return code, err.getvalue()

    def test_endpoint_is_created_with_env_defaults(self) -> None:
        code, err = self.run_cli(
            "routes",
            "add-model-studio-endpoint",
            "ms",
            "--renews-on",
            "2026-09-12",
            extra_env={
                "MODEL_STUDIO_PLAN": "token-plan-personal",
                "MODEL_STUDIO_TIER": "pro",
                "MODEL_STUDIO_TOKEN_PLAN_AUTOMATION": "accept",
            },
        )
        self.assertEqual(0, code, err)
        endpoint = json.loads(self.routes.read_text(encoding="utf-8"))["endpoints"]["ms"]
        self.assertEqual("MODEL_STUDIO_API_KEY", endpoint["apiKeyEnv"])
        self.assertEqual("accept", endpoint["tokenPlanAutomation"])
        self.assertEqual(8, endpoint["concurrency"])

    def test_automation_env_does_not_leak_onto_pay_as_you_go(self) -> None:
        code, err = self.run_cli(
            "routes",
            "add-model-studio-endpoint",
            "payg",
            "--plan",
            "pay-as-you-go",
            "--region",
            "ap-southeast-1",
            extra_env={"MODEL_STUDIO_TOKEN_PLAN_AUTOMATION": "accept"},
        )
        self.assertEqual(0, code, err)
        self.assertNotIn(
            "tokenPlanAutomation",
            json.loads(self.routes.read_text(encoding="utf-8"))["endpoints"]["payg"],
        )

    def test_route_add_fills_limits_and_pins_the_harness(self) -> None:
        self.run_cli(
            "routes",
            "add-model-studio-endpoint",
            "ms",
            "--plan",
            "token-plan-personal",
            "--tier",
            "pro",
        )
        code, err = self.run_cli(
            "routes", "add", "flash", "--model", "qwen3.8-flash", "--endpoint", "ms"
        )
        self.assertEqual(0, code, err)
        route = json.loads(self.routes.read_text(encoding="utf-8"))["models"]["flash"]
        self.assertEqual({"context": 991808, "output": 131072}, route["limits"])
        self.assertEqual("opencode", route["harness"])

    def test_model_without_catalog_limits_needs_explicit_limits(self) -> None:
        self.run_cli(
            "routes",
            "add-model-studio-endpoint",
            "ms",
            "--plan",
            "token-plan-personal",
            "--tier",
            "pro",
        )
        code, err = self.run_cli("routes", "add", "router", "--model", "auto", "--endpoint", "ms")
        self.assertEqual(2, code)
        self.assertIn("--limits", err)

    def test_invalid_endpoint_is_refused_without_writing(self) -> None:
        code, err = self.run_cli(
            "routes",
            "add-model-studio-endpoint",
            "ms",
            "--plan",
            "token-plan-personal",
            "--tier",
            "pro",
            "--region",
            "us-east-1",
        )
        self.assertEqual(2, code)
        self.assertIn("region_not_allowed", err)
        self.assertFalse(self.routes.exists())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd packages/agent-routing && .venv/bin/python -m unittest tests.test_cli_model_studio 2>&1 | tail -4`
Expected: FAIL; argparse error `invalid choice: 'add-model-studio-endpoint'` (exit 2 on the first test with that message).

- [ ] **Step 3: Implement**

Add `from . import model_studio` to the `cli.py` imports. Add the parser after the `routes_add` block (after line 1344):

```python
ms_endpoint = routes_commands.add_parser("add-model-studio-endpoint")
ms_endpoint.add_argument("name")
ms_endpoint.add_argument(
    "--plan", choices=("token-plan-personal", "token-plan-team", "pay-as-you-go")
)
ms_endpoint.add_argument("--api-key-env", dest="api_key_env")
ms_endpoint.add_argument("--region")
ms_endpoint.add_argument("--model-studio-workspace", dest="model_studio_workspace")
ms_endpoint.add_argument("--protocol", choices=("openai", "anthropic"))
ms_endpoint.add_argument("--tier")
ms_endpoint.add_argument("--concurrency", type=int)
ms_endpoint.add_argument("--renews-on", dest="renews_on", metavar="YYYY-MM-DD")
ms_endpoint.add_argument(
    "--accept-token-plan-automation", action="store_true", dest="accept_token_plan_automation"
)
```

Route the new subcommand beside `if args.routes_command == "add":` (line 1456):

```python
        if args.routes_command == "add-model-studio-endpoint":
            return _routes_add_model_studio_endpoint(args)
```

Add the handler after `_routes_add`:

```python
def _routes_add_model_studio_endpoint(args: argparse.Namespace) -> int:
    env = os.environ
    plan = args.plan or env.get("MODEL_STUDIO_PLAN")
    raw: dict[str, Any] = {
        "kind": model_studio.KIND,
        "apiKeyEnv": args.api_key_env or "MODEL_STUDIO_API_KEY",
    }
    for key, value in (
        ("plan", plan),
        ("tier", args.tier or env.get("MODEL_STUDIO_TIER")),
        ("region", args.region or env.get("MODEL_STUDIO_REGION")),
        ("workspace", args.model_studio_workspace or env.get("MODEL_STUDIO_WORKSPACE_ID")),
        ("protocol", args.protocol),
        ("renewsOn", args.renews_on or env.get("MODEL_STUDIO_RENEWS_ON")),
    ):
        if value:
            raw[key] = value
    if args.concurrency is not None:
        raw["concurrency"] = args.concurrency
    token_plan = (
        model_studio.load_catalog()["plans"].get(plan or "", {}).get("family") == "token-plan"
    )
    if token_plan and (
        args.accept_token_plan_automation
        or env.get(model_studio.AUTOMATION_ENV) == model_studio.AUTOMATION_ACCEPT
    ):
        raw["tokenPlanAutomation"] = model_studio.AUTOMATION_ACCEPT
    try:
        registry, config, _home = _routes_context()
        updated: dict[str, Any] = json.loads(json.dumps(config))
        updated.setdefault("endpoints", {})[args.name] = raw
        path = save_routes(os.environ, updated, registry=registry)
    except (RoutesError, RegistryError) as exc:
        print(f"pitwall-agent-routing: {exc}", file=sys.stderr)
        return 2
    print(f"saved endpoint {args.name} to {path}")
    if token_plan and "tokenPlanAutomation" not in raw:
        print(
            "note: headless dispatch through this Token Plan endpoint is refused until you accept the "
            "automation risk (--accept-token-plan-automation); the Token Plan terms allow interactive use only",
        )
    return 0
```

In `_routes_add`, immediately before `try:` / `updated = add_route(` (line 824), insert:

```python
harness = args.harness
if endpoint is not None:
    shared = config.get("endpoints", {}).get(endpoint)
    if isinstance(shared, Mapping) and shared.get("kind") == model_studio.KIND:
        if limits is None:
            try:
                limits = model_studio.route_limits(model)
            except model_studio.ModelStudioError as exc:
                print(f"pitwall-agent-routing: {exc}", file=sys.stderr)
                return 2
            if limits is None:
                print(
                    f"pitwall-agent-routing: the catalog publishes no limits for {model!r}; pass --limits",
                    file=sys.stderr,
                )
                return 2
        harness = harness or str(config["defaults"]["endpointHarness"])
```

and pass `harness=harness` instead of `harness=args.harness` in the `add_route(...)` call. Add `Mapping` and `Any` to the `typing` import if they are not already imported, and `json` if not imported.

- [ ] **Step 4: Run tests**

```bash
cd packages/agent-routing
.venv/bin/python -m unittest tests.test_cli_model_studio -v 2>&1 | tail -4
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
```

Expected: `Ran 5 tests … OK`; full discover `OK`.

- [ ] **Step 5: Commit**

```bash
git add packages/agent-routing/runtime/model_routing/cli.py packages/agent-routing/tests/test_cli_model_studio.py
git commit -m "feat(agent-routing): add Model Studio endpoints and catalog-limited routes from the CLI"
```

---

### Task 6: Harness sync for Model Studio endpoints (OpenCode protocol, usage, variants; Pi reasoning)

**Files:**
- Modify: `packages/agent-routing/runtime/model_routing/providers/opencode.py` `plan_endpoint_sync` (lines 85–108)
- Modify: `packages/agent-routing/runtime/model_routing/providers/pi.py` `_managed_model` (lines 88–94)
- Test: `packages/agent-routing/tests/test_route_sync_model_studio.py`

**Interfaces:**
- Consumes: Task 3 `KIND`, `effort_values`; Task 4 normalized entries.
- Produces: OpenCode provider blocks with `npm` per protocol, `options.includeUsage` (openai), `models.<id>.variants` per effort value; Pi models with `reasoning: true` when the catalog model has an effort control.

- [ ] **Step 1: Write the failing tests** (`packages/agent-routing/tests/test_route_sync_model_studio.py`)

```python
"""Harness sync writes Model Studio endpoints with protocol, usage, limits, and effort."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from model_routing import routes  # noqa: E402
from model_routing.providers import get_adapter  # noqa: E402
from model_routing.registry import load_registry  # noqa: E402

REGISTRY = load_registry()


def entries(protocol: str = "openai", harness: str = "opencode") -> dict[str, object]:
    doc = {
        "schemaVersion": 1,
        "endpoints": {
            "ms": {
                "kind": "model-studio",
                "plan": "token-plan-personal",
                "tier": "pro",
                "apiKeyEnv": "MODEL_STUDIO_API_KEY",
                "protocol": protocol,
            }
        },
        "models": {
            "flash": {
                "model": "qwen3.8-flash",
                "endpoint": "ms",
                "harness": harness,
                "limits": {"context": 991808, "output": 131072},
            }
        },
    }
    return routes.validate_routes(doc, registry=REGISTRY)["models"]


class OpenCodeSyncTests(unittest.TestCase):
    def plan(self, protocol: str) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as tmp:
            _path, _before, after = get_adapter("opencode").plan_endpoint_sync(
                entries(protocol), {"XDG_CONFIG_HOME": tmp}, Path(tmp)
            )
        return json.loads(after)["provider"]["flash"]

    def test_openai_protocol_block(self) -> None:
        block = self.plan("openai")
        self.assertEqual("@ai-sdk/openai-compatible", block["npm"])
        self.assertEqual(
            "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
            block["options"]["baseURL"],
        )
        self.assertIs(True, block["options"]["includeUsage"])
        self.assertEqual("{env:MODEL_STUDIO_API_KEY}", block["options"]["apiKey"])
        model = block["models"]["qwen3.8-flash"]
        self.assertEqual({"context": 991808, "output": 131072}, model["limit"])
        self.assertEqual({"reasoningEffort": "medium"}, model["variants"]["medium"])

    def test_anthropic_protocol_block(self) -> None:
        block = self.plan("anthropic")
        self.assertEqual("@ai-sdk/anthropic", block["npm"])
        self.assertEqual(
            "https://token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic/v1",
            block["options"]["baseURL"],
        )
        self.assertNotIn("includeUsage", block["options"])
        self.assertNotIn("variants", block["models"]["qwen3.8-flash"])


class PiSyncTests(unittest.TestCase):
    def test_reasoning_flag_for_models_with_effort(self) -> None:
        model = get_adapter("pi")._managed_model(entries(harness="pi")["flash"])
        self.assertIs(True, model["reasoning"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd packages/agent-routing && .venv/bin/python -m unittest tests.test_route_sync_model_studio 2>&1 | tail -4`
Expected: FAIL with `KeyError: 'includeUsage'` and `KeyError: 'reasoning'`.

- [ ] **Step 3: Implement**

In `providers/opencode.py`, add `from .. import model_studio` and replace the loop body from `options: dict[str, str] = ...` through the `providers[name] = {...}` assignment (lines 90–108) with:

```python
            endpoint = entry["endpoint"]
            is_model_studio = endpoint.get("kind") == model_studio.KIND
            protocol = str(endpoint.get("protocol", "openai")) if is_model_studio else "openai"
            npm = "@ai-sdk/anthropic" if protocol == "anthropic" else "@ai-sdk/openai-compatible"
            base = str(endpoint["baseUrl"]).rstrip("/") + ("/v1" if protocol == "anthropic" else "")
            options: dict[str, Any] = {"baseURL": base}
            key_env = endpoint.get("apiKeyEnv")
            if key_env:
                options["apiKey"] = f"{{env:{key_env}}}"
            if is_model_studio and protocol == "openai":
                options["includeUsage"] = True
            limits = entry.get("limits", {})
            model_id = str(entry["model"])
            model_block: dict[str, Any] = {
                "name": model_id,
                "limit": {"context": limits.get("context", 32768), "output": limits.get("output", 4096)},
            }
            if is_model_studio and protocol == "openai":
                values = model_studio.effort_values(model_id)
                if values:
                    model_block["variants"] = {value: {"reasoningEffort": value} for value in values}
            providers[name] = {
                "npm": npm,
                "name": f"{self.MANAGED_PREFIX}{name}",
                "options": options,
                "models": {model_id: model_block},
            }
```

(`Any` is already imported in `opencode.py`; add it to the `typing` import if not.)

In `providers/pi.py`, add `from .. import model_studio` and replace `_managed_model`:

```python
@staticmethod
def _managed_model(entry: Mapping[str, Any]) -> dict[str, Any]:
    limits = entry.get("limits", {})
    model: dict[str, Any] = {
        "id": str(entry["model"]),
        "contextWindow": limits.get("context", 32768),
        "maxTokens": limits.get("output", 4096),
    }
    if entry["endpoint"].get("kind") == model_studio.KIND and model_studio.effort_values(
        str(entry["model"])
    ):
        model["reasoning"] = True
    return model
```

- [ ] **Step 4: Run tests**

```bash
cd packages/agent-routing
.venv/bin/python -m unittest tests.test_route_sync_model_studio tests.test_route_sync -v 2>&1 | tail -4
```

Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add packages/agent-routing/runtime/model_routing/providers/opencode.py packages/agent-routing/runtime/model_routing/providers/pi.py packages/agent-routing/tests/test_route_sync_model_studio.py
git commit -m "feat(agent-routing): sync Model Studio endpoints with protocol, usage, and effort variants"
```

---

### Task 7: Dispatch gate, concurrency slots, and the local exhaustion lockout

**Files:**
- Create: `packages/agent-routing/runtime/model_routing/endpoint_slots.py`
- Modify: `packages/agent-routing/runtime/model_routing/routes.py` `dispatch_route` (lines 953–999) and add `_model_studio_preflight`
- Test: `packages/agent-routing/tests/test_endpoint_slots.py`, `packages/agent-routing/tests/test_dispatch_model_studio.py`

**Interfaces:**
- Consumes: Task 3 `check_key`, `automation_accepted`, `is_token_plan`, `credits_window`, `EXHAUSTED_MESSAGE`, `AUTOMATION_ENV`; `state_root`, `find_run`.
- Produces:
  - `endpoint_slots.EX_TEMPFAIL = 75`
  - `class EndpointBusy(RuntimeError)`
  - `acquire_slot(env, endpoint: str, capacity: int, *, wait_seconds: float, poll_seconds: float = 0.25) -> ContextManager[int]`
  - `lockout_path(env, endpoint: str) -> Path`, `read_lockout(env, endpoint, *, now) -> str | None` (ISO `until` while locked), `write_lockout(env, endpoint, until: datetime) -> None`
  - `output_reports_exhaustion(run_dir: Path) -> bool`

- [ ] **Step 1: Write the slot tests** (`packages/agent-routing/tests/test_endpoint_slots.py`)

```python
"""Endpoint slots cap concurrent dispatches and survive crashed holders."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from model_routing import endpoint_slots as slots  # noqa: E402


class SlotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {"SUBAGENT_MODEL_ROUTING_STATE_HOME": self.tmp.name}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_capacity_is_enforced_then_freed(self) -> None:
        with slots.acquire_slot(self.env, "ms", 1, wait_seconds=0):
            with self.assertRaises(slots.EndpointBusy):
                with slots.acquire_slot(self.env, "ms", 1, wait_seconds=0.3, poll_seconds=0.05):
                    pass
        with slots.acquire_slot(self.env, "ms", 1, wait_seconds=0) as index:
            self.assertEqual(0, index)

    def test_slot_is_released_when_holder_process_dies(self) -> None:
        code = (
            "import sys, time; sys.path.insert(0, sys.argv[1]);"
            "from model_routing import endpoint_slots as s;"
            "cm = s.acquire_slot({'SUBAGENT_MODEL_ROUTING_STATE_HOME': sys.argv[2]}, 'ms', 1, wait_seconds=0);"
            "cm.__enter__(); print('held', flush=True); time.sleep(60)"
        )
        holder = subprocess.Popen(
            [sys.executable, "-c", code, str(ROOT / "runtime"), self.tmp.name],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertEqual("held", holder.stdout.readline().strip())  # type: ignore[union-attr]
            with self.assertRaises(slots.EndpointBusy):
                with slots.acquire_slot(self.env, "ms", 1, wait_seconds=0):
                    pass
            holder.kill()
            holder.wait(timeout=10)
            with slots.acquire_slot(self.env, "ms", 1, wait_seconds=2):
                pass
        finally:
            if holder.poll() is None:
                holder.kill()

    def test_lockout_round_trip(self) -> None:
        now = datetime(2026, 9, 26, tzinfo=timezone.utc)
        self.assertIsNone(slots.read_lockout(self.env, "ms", now=now))
        slots.write_lockout(self.env, "ms", now + timedelta(days=3))
        self.assertEqual("2026-09-29T00:00:00+00:00", slots.read_lockout(self.env, "ms", now=now))
        self.assertIsNone(slots.read_lockout(self.env, "ms", now=now + timedelta(days=4)))

    def test_exhaustion_is_detected_in_run_output(self) -> None:
        run = Path(self.tmp.name) / "run"
        run.mkdir()
        (run / "stdout.log").write_text("ok\n", encoding="utf-8")
        (run / "stderr.log").write_text(
            "Error: 429 insufficient_quota: Your token-plan quota has been exhausted.\n",
            encoding="utf-8",
        )
        self.assertTrue(slots.output_reports_exhaustion(run))
        (run / "stderr.log").write_text("", encoding="utf-8")
        self.assertFalse(slots.output_reports_exhaustion(run))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Write the dispatch tests** (`packages/agent-routing/tests/test_dispatch_model_studio.py`)

```python
"""dispatch_route gates Token Plan automation, key pairing, lockouts, and concurrency."""

from __future__ import annotations

from contextlib import redirect_stderr
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from model_routing import endpoint_slots, model_studio, routes  # noqa: E402

ENDPOINT = model_studio.validate_endpoint(
    {
        "kind": "model-studio",
        "plan": "token-plan-personal",
        "tier": "pro",
        "apiKeyEnv": "MODEL_STUDIO_API_KEY",
        "renewsOn": "2026-09-12",
    },
    "e",
)


def resolved(endpoint: dict[str, object]) -> SimpleNamespace:
    entry = {"model": "qwen3.8-flash", "endpoint": routes.EndpointReference("ms", endpoint)}
    return SimpleNamespace(
        harness="opencode",
        name="flash",
        model="qwen3.8-flash",
        endpoint_host="token-plan",
        notices=(),
        env_updates={},
        argv=("p.md",),
        entry=entry,
    )


class DispatchGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def dispatch(
        self, endpoint: dict[str, object], key: str = "sk-sp-x", **extra_env: str
    ) -> tuple[int, str, mock.Mock]:
        env = {
            "HOME": self.tmp.name,
            "SUBAGENT_MODEL_ROUTING_STATE_HOME": self.tmp.name,
            "MODEL_STUDIO_API_KEY": key,
            **extra_env,
        }
        stderr = io.StringIO()
        with (
            mock.patch.object(routes, "load_registry", return_value={}),
            mock.patch.object(routes, "load_routes", return_value={"models": {}}),
            mock.patch.object(routes, "resolve_route", return_value=resolved(endpoint)),
            mock.patch.object(routes, "dispatch_legacy", return_value=0) as legacy,
            redirect_stderr(stderr),
        ):
            code = routes.dispatch_route(["flash", "p.md"], environ=env)
        return code, stderr.getvalue(), legacy

    def test_token_plan_without_acceptance_is_refused(self) -> None:
        code, err, legacy = self.dispatch(ENDPOINT)
        self.assertEqual(routes.EX_CONFIG, code)
        self.assertIn("automation_not_accepted", err)
        self.assertIn(model_studio.AUTOMATION_ENV, err)
        legacy.assert_not_called()

    def test_wrong_key_is_refused_without_echoing_it(self) -> None:
        code, err, legacy = self.dispatch(
            {**ENDPOINT, "tokenPlanAutomation": "accept"}, key="sk-secretvalue"
        )
        self.assertEqual(routes.EX_CONFIG, code)
        self.assertIn("key_plan_mismatch", err)
        self.assertNotIn("secretvalue", err)
        legacy.assert_not_called()

    def test_accepted_dispatch_runs_inside_a_slot_with_a_dispatch_id(self) -> None:
        code, _err, legacy = self.dispatch({**ENDPOINT, "tokenPlanAutomation": "accept"})
        self.assertEqual(0, code)
        self.assertIn("SUBAGENT_MODEL_ROUTING_DISPATCH_ID", legacy.call_args.kwargs["environ"])

    def test_busy_endpoint_is_refused_with_tempfail(self) -> None:
        accepted = {**ENDPOINT, "tokenPlanAutomation": "accept", "concurrency": 1}
        env = {"SUBAGENT_MODEL_ROUTING_STATE_HOME": self.tmp.name}
        with endpoint_slots.acquire_slot(env, "ms", 1, wait_seconds=0):
            code, err, legacy = self.dispatch(accepted, SHIM_TIMEOUT_SECS="0.2")
        self.assertEqual(endpoint_slots.EX_TEMPFAIL, code)
        self.assertIn("endpoint_busy", err)
        legacy.assert_not_called()

    def test_locked_endpoint_is_refused_until_renewal(self) -> None:
        from datetime import datetime, timedelta, timezone

        env = {"SUBAGENT_MODEL_ROUTING_STATE_HOME": self.tmp.name}
        endpoint_slots.write_lockout(env, "ms", datetime.now(timezone.utc) + timedelta(days=1))
        code, err, legacy = self.dispatch({**ENDPOINT, "tokenPlanAutomation": "accept"})
        self.assertEqual(endpoint_slots.EX_TEMPFAIL, code)
        self.assertIn("credits_exhausted", err)
        legacy.assert_not_called()

    def test_routes_without_an_entry_attribute_still_dispatch(self) -> None:
        plain = SimpleNamespace(
            harness="codex",
            name="c",
            model="m",
            endpoint_host=None,
            notices=(),
            env_updates={},
            argv=("p.md",),
        )
        with (
            mock.patch.object(routes, "load_registry", return_value={}),
            mock.patch.object(routes, "load_routes", return_value={"models": {}}),
            mock.patch.object(routes, "resolve_route", return_value=plain),
            mock.patch.object(routes, "dispatch_legacy", return_value=0),
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(
                0, routes.dispatch_route(["c", "p.md"], environ={"HOME": self.tmp.name})
            )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run to verify failure**

Run: `cd packages/agent-routing && .venv/bin/python -m unittest tests.test_endpoint_slots tests.test_dispatch_model_studio 2>&1 | tail -4`
Expected: `ModuleNotFoundError: No module named 'model_routing.endpoint_slots'`.

- [ ] **Step 4: Implement `endpoint_slots.py`**

```python
"""Per-endpoint concurrency slots and the local Token Plan exhaustion lockout (stdlib only)."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import time
from typing import Iterator, Mapping

from .model_studio import EXHAUSTED_MESSAGE
from .run_store import atomic_write_json, ensure_private_directory, state_root

EX_TEMPFAIL = 75
_TAIL_BYTES = 64 * 1024


class EndpointBusy(RuntimeError):
    pass


def _endpoint_dir(env: Mapping[str, str], endpoint: str) -> Path:
    return state_root(env) / "endpoint-slots" / endpoint


@contextmanager
def acquire_slot(
    env: Mapping[str, str],
    endpoint: str,
    capacity: int,
    *,
    wait_seconds: float,
    poll_seconds: float = 0.25,
) -> Iterator[int]:
    """Hold one of ``capacity`` flock slots; the kernel frees it if the holder dies."""
    directory = _endpoint_dir(env, endpoint)
    ensure_private_directory(directory)
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while True:
        for index in range(capacity):
            descriptor = os.open(directory / f"{index}.lock", os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(descriptor)
                continue
            try:
                yield index
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
            return
        if time.monotonic() >= deadline:
            raise EndpointBusy(
                f"endpoint_busy: all {capacity} slots of endpoint {endpoint!r} are in use"
            )
        time.sleep(poll_seconds)


def lockout_path(env: Mapping[str, str], endpoint: str) -> Path:
    return _endpoint_dir(env, endpoint) / "exhausted.json"


def write_lockout(env: Mapping[str, str], endpoint: str, until: datetime) -> None:
    path = lockout_path(env, endpoint)
    ensure_private_directory(path.parent)
    atomic_write_json(path, {"until": until.isoformat()})


def read_lockout(env: Mapping[str, str], endpoint: str, *, now: datetime) -> str | None:
    try:
        until = str(json.loads(lockout_path(env, endpoint).read_text(encoding="utf-8"))["until"])
        locked = datetime.fromisoformat(until) > now
    except OSError, ValueError, KeyError, TypeError:
        return None
    return until if locked else None


def output_reports_exhaustion(run_dir: Path) -> bool:
    for name in ("stderr.log", "stdout.log"):
        path = run_dir / name
        try:
            with path.open("rb") as handle:
                handle.seek(max(0, path.stat().st_size - _TAIL_BYTES))
                tail = handle.read().decode("utf-8", errors="replace").lower()
        except OSError:
            continue
        if EXHAUSTED_MESSAGE in tail:
            return True
    return False
```

- [ ] **Step 5: Implement the gate in `routes.py`**

Add imports: `import uuid`, `from datetime import timedelta` (extend the existing `datetime` import), `from . import endpoint_slots`, and `from .run_store import find_run` (extend the existing `run_store` import). Add the helper above `dispatch_route`:

```python
def _model_studio_preflight(
    resolved: Any, env: Mapping[str, str]
) -> tuple[str, Mapping[str, Any]] | None:
    """Return (endpoint name, endpoint) for a Model Studio route after its gates, else None."""
    entry = getattr(resolved, "entry", None) or {}
    endpoint = resolved_endpoint(entry)
    if endpoint is None or endpoint.get("kind") != model_studio.KIND:
        return None
    raw = entry.get("endpoint")
    name = (
        str(raw) if isinstance(raw, EndpointReference) else str(getattr(resolved, "name", "route"))
    )
    plan = str(endpoint["plan"])
    if model_studio.is_token_plan(plan) and not model_studio.automation_accepted(endpoint):
        raise RouteConfigError(
            f"automation_not_accepted: endpoint {name!r} is a Token Plan endpoint and the Token Plan terms allow "
            f"interactive use only; to accept that risk for headless dispatch run "
            f"`pitwall-agent-routing routes add-model-studio-endpoint {name} ... --accept-token-plan-automation` "
            f"(or set {model_studio.AUTOMATION_ENV}=accept when creating it)"
        )
    key = env.get(str(endpoint["apiKeyEnv"]), "")
    if not key:
        raise RouteConfigError(f"missing_key: export {endpoint['apiKeyEnv']} for endpoint {name!r}")
    try:
        model_studio.check_key(plan, key)
    except model_studio.ModelStudioError as exc:
        raise RouteConfigError(
            f"{exc} (endpoint {name!r}, key from {endpoint['apiKeyEnv']})"
        ) from exc
    return name, endpoint
```

In `dispatch_route`, add `model_studio_target = _model_studio_preflight(resolved, env)` as the last statement inside the `try:` block (after the `revived` handling; `RouteConfigError` is already caught and mapped to `EX_CONFIG`). Replace the final three lines (`child_env = dict(env)` … `return dispatch_legacy(...)`) with:

```python
child_env = dict(env)
child_env.update(resolved.env_updates)
if model_studio_target is None:
    return dispatch_legacy(resolved.harness, list(resolved.argv), environ=child_env, route=resolved)
endpoint_name, endpoint = model_studio_target
locked_until = endpoint_slots.read_lockout(env, endpoint_name, now=datetime.now(timezone.utc))
if locked_until is not None:
    print(
        f"route-shim: credits_exhausted: endpoint {endpoint_name!r} is locked until {locked_until}",
        file=sys.stderr,
    )
    _emit_sentinel(endpoint_slots.EX_TEMPFAIL, leading_newline=False)
    return endpoint_slots.EX_TEMPFAIL
dispatch_id = child_env.setdefault("SUBAGENT_MODEL_ROUTING_DISPATCH_ID", str(uuid.uuid4()))
capacity = int(endpoint.get("concurrency") or 1_000_000)
try:
    wait_seconds = float(env.get("SHIM_TIMEOUT_SECS", "1140"))
except ValueError:
    wait_seconds = 1140.0
try:
    with endpoint_slots.acquire_slot(env, endpoint_name, capacity, wait_seconds=wait_seconds):
        code = dispatch_legacy(
            resolved.harness, list(resolved.argv), environ=child_env, route=resolved
        )
except endpoint_slots.EndpointBusy as exc:
    print(f"route-shim: {exc}", file=sys.stderr)
    _emit_sentinel(endpoint_slots.EX_TEMPFAIL, leading_newline=False)
    return endpoint_slots.EX_TEMPFAIL
if code != 0 and model_studio.is_token_plan(str(endpoint["plan"])):
    try:
        exhausted = endpoint_slots.output_reports_exhaustion(find_run(env, dispatch_id))
    except FileNotFoundError:
        exhausted = False
    if exhausted:
        now = datetime.now(timezone.utc)
        renews_on = endpoint.get("renewsOn")
        until = (
            model_studio.credits_window(str(renews_on), now)[1]
            if renews_on
            else now + timedelta(days=1)
        )
        endpoint_slots.write_lockout(env, endpoint_name, until)
        print(
            f"route-shim: credits_exhausted: endpoint {endpoint_name!r} locked until {until.isoformat()}",
            file=sys.stderr,
        )
return code
```

`model_studio_target` must be initialized to `None` before the `try:` so the non-exception path always has it.

- [ ] **Step 6: Run tests**

```bash
cd packages/agent-routing
.venv/bin/python -m unittest tests.test_endpoint_slots tests.test_dispatch_model_studio tests.test_managed_route_pin -v 2>&1 | tail -4
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
```

Expected: `OK` for the three modules; full discover `OK`.

- [ ] **Step 7: Commit**

```bash
git add packages/agent-routing/runtime/model_routing/endpoint_slots.py packages/agent-routing/runtime/model_routing/routes.py packages/agent-routing/tests/test_endpoint_slots.py packages/agent-routing/tests/test_dispatch_model_studio.py
git commit -m "feat(agent-routing): Token Plan automation gate, endpoint slots, and exhaustion lockout"
```

---

### Task 8: Stdlib ACS3 signer and the subscription-stats read (Agent Routing)

**Files:**
- Create: `packages/agent-routing/runtime/model_routing/model_studio_openapi.py`
- Test: `packages/agent-routing/tests/test_model_studio_openapi.py`

**Interfaces:**
- Consumes: Task 3 `load_catalog()["openApi"]`.
- Produces:
  - `ACCESS_KEY_ID_ENV = "ALIBABA_CLOUD_ACCESS_KEY_ID"`, `ACCESS_KEY_SECRET_ENV = "ALIBABA_CLOUD_ACCESS_KEY_SECRET"`
  - `acs3_authorization(method, host, path, query: Mapping[str, str], headers: Mapping[str, str], body: bytes, *, access_key_id, access_key_secret) -> str`
  - `@dataclass(frozen=True) class SubscriptionStats: window_start: datetime; reset_at: datetime; total_credits: float; remaining_credits: float`
  - `get_subscription_stats(env, *, now: datetime, nonce: str | None = None, opener=urllib.request.urlopen, timeout: float = 10.0) -> SubscriptionStats | None` (None when the AccessKey is not configured)

- [ ] **Step 1: Write the failing tests** (`packages/agent-routing/tests/test_model_studio_openapi.py`)

The expected signature is the reference vector shared with the broker (Task 16 asserts the same value).

```python
"""ACS3-HMAC-SHA256 signing and GetSubscriptionStats parsing."""

from __future__ import annotations

from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from model_routing import model_studio_openapi as api  # noqa: E402

VECTOR_SIGNATURE = "3e90126c0a15bd300a99078246f80342877fa314c10ff4275277d6d5ba98a0ce"


class SignerTests(unittest.TestCase):
    def test_reference_vector(self) -> None:
        header = api.acs3_authorization(
            "GET",
            "modelstudio.ap-southeast-1.aliyuncs.com",
            "/tokenplan/subscription/stats",
            {},
            {
                "x-acs-action": "GetSubscriptionStats",
                "x-acs-version": "2026-02-10",
                "x-acs-date": "2026-09-26T12:00:00Z",
                "x-acs-signature-nonce": "3f1d6c2e-0000-4000-8000-000000000001",
            },
            b"",
            access_key_id="TESTAKID",
            access_key_secret="test-secret",
        )
        self.assertEqual(
            "ACS3-HMAC-SHA256 Credential=TESTAKID,SignedHeaders=host;x-acs-action;x-acs-content-sha256;"
            f"x-acs-date;x-acs-signature-nonce;x-acs-version,Signature={VECTOR_SIGNATURE}",
            header,
        )


class _Response(io.BytesIO):
    status = 200

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class StatsTests(unittest.TestCase):
    def test_without_access_key_returns_none(self) -> None:
        self.assertIsNone(api.get_subscription_stats({}, now=datetime.now(timezone.utc)))

    def test_parses_items_and_refresh_time(self) -> None:
        captured: dict[str, object] = {}
        body = {
            "Success": True,
            "Data": {
                "SubscriptionStartTime": 1757000000000,
                "SubscriptionEndTime": 1790000000000,
                "Items": [
                    {
                        "SeatType": "pro",
                        "SeatRefreshTime": 1759622400000,
                        "TotalSeats": 1,
                        "AssignedSeats": 1,
                        "SeatCredits": 180000,
                        "SeatRemainingCredits": 123456.5,
                    }
                ],
            },
        }

        def opener(request: object, timeout: float) -> _Response:
            captured["request"] = request
            return _Response(json.dumps(body).encode())

        stats = api.get_subscription_stats(
            {api.ACCESS_KEY_ID_ENV: "AKID", api.ACCESS_KEY_SECRET_ENV: "secret"},
            now=datetime(2026, 9, 26, tzinfo=timezone.utc),
            nonce="n-1",
            opener=opener,
        )
        assert stats is not None
        self.assertEqual(180000.0, stats.total_credits)
        self.assertEqual(123456.5, stats.remaining_credits)
        self.assertEqual(datetime.fromtimestamp(1759622400, tz=timezone.utc), stats.reset_at)
        request = captured["request"]
        self.assertTrue(
            request.get_header("Authorization").startswith("ACS3-HMAC-SHA256 Credential=AKID,")
        )  # type: ignore[attr-defined]
        self.assertNotIn("secret", request.get_header("Authorization"))  # type: ignore[attr-defined]


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd packages/agent-routing && .venv/bin/python -m unittest tests.test_model_studio_openapi 2>&1 | tail -3`
Expected: `ModuleNotFoundError: No module named 'model_routing.model_studio_openapi'`.

- [ ] **Step 3: Implement** (`model_studio_openapi.py`)

```python
"""Model Studio OpenAPI reads with ACS3-HMAC-SHA256 signing (stdlib only)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from typing import Any, Callable, Mapping
from urllib import request
from urllib.parse import quote
import uuid

from .model_studio import load_catalog

ACCESS_KEY_ID_ENV = "ALIBABA_CLOUD_ACCESS_KEY_ID"
ACCESS_KEY_SECRET_ENV = "ALIBABA_CLOUD_ACCESS_KEY_SECRET"
_ALGORITHM = "ACS3-HMAC-SHA256"


def _encode(value: str) -> str:
    return quote(value, safe="-_.~")


def acs3_authorization(
    method: str,
    host: str,
    path: str,
    query: Mapping[str, str],
    headers: Mapping[str, str],
    body: bytes,
    *,
    access_key_id: str,
    access_key_secret: str,
) -> str:
    payload_hash = hashlib.sha256(body).hexdigest()
    canonical_headers = {key.lower(): value.strip() for key, value in headers.items()}
    canonical_headers["host"] = host
    canonical_headers["x-acs-content-sha256"] = payload_hash
    names = sorted(
        name
        for name in canonical_headers
        if name in {"host", "content-type"} or name.startswith("x-acs-")
    )
    header_block = "".join(f"{name}:{canonical_headers[name]}\n" for name in names)
    signed = ";".join(names)
    query_block = "&".join(
        f"{_encode(key)}={_encode(value)}" for key, value in sorted(query.items())
    )
    canonical = "\n".join(
        [method, quote(path or "/", safe="/-_.~"), query_block, header_block, signed, payload_hash]
    )
    string_to_sign = f"{_ALGORITHM}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
    signature = hmac.new(
        access_key_secret.encode(), string_to_sign.encode(), hashlib.sha256
    ).hexdigest()
    return f"{_ALGORITHM} Credential={access_key_id},SignedHeaders={signed},Signature={signature}"


@dataclass(frozen=True, slots=True)
class SubscriptionStats:
    window_start: datetime
    reset_at: datetime
    total_credits: float
    remaining_credits: float


def _ms(value: Any) -> datetime:
    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)


def get_subscription_stats(
    env: Mapping[str, str],
    *,
    now: datetime,
    nonce: str | None = None,
    opener: Callable[..., Any] = request.urlopen,
    timeout: float = 10.0,
) -> SubscriptionStats | None:
    key_id = env.get(ACCESS_KEY_ID_ENV, "")
    secret = env.get(ACCESS_KEY_SECRET_ENV, "")
    if not key_id or not secret:
        return None
    openapi = load_catalog()["openApi"]
    host = str(openapi["host"])
    path = "/tokenplan/subscription/stats"
    headers = {
        "x-acs-action": "GetSubscriptionStats",
        "x-acs-version": str(openapi["version"]),
        "x-acs-date": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "x-acs-signature-nonce": nonce or str(uuid.uuid4()),
    }
    authorization = acs3_authorization(
        "GET", host, path, {}, headers, b"", access_key_id=key_id, access_key_secret=secret
    )
    outbound = request.Request(
        f"https://{host}{path}",
        headers={
            **headers,
            "x-acs-content-sha256": hashlib.sha256(b"").hexdigest(),
            "Authorization": authorization,
            "Accept": "application/json",
        },
        method="GET",
    )
    with opener(outbound, timeout=timeout) as response:
        payload = json.loads(response.read(1024 * 1024).decode("utf-8"))
    data = payload.get("Data") or {}
    items = data.get("Items") or []
    if not items:
        return None
    total = sum(float(item.get("SeatCredits") or 0) for item in items)
    remaining = sum(float(item.get("SeatRemainingCredits") or 0) for item in items)
    reset_at = _ms(min(int(item["SeatRefreshTime"]) for item in items))
    return SubscriptionStats(
        window_start=reset_at - timedelta(days=30),
        reset_at=reset_at,
        total_credits=total,
        remaining_credits=remaining,
    )
```

- [ ] **Step 4: Run tests**

Run: `cd packages/agent-routing && .venv/bin/python -m unittest tests.test_model_studio_openapi -v 2>&1 | tail -4`
Expected: `Ran 3 tests … OK`.

- [ ] **Step 5: Commit**

```bash
git add packages/agent-routing/runtime/model_routing/model_studio_openapi.py packages/agent-routing/tests/test_model_studio_openapi.py
git commit -m "feat(agent-routing): stdlib ACS3 signer and Token Plan subscription stats"
```

---

### Task 9: Readiness — `routes probe` and `doctor` for Model Studio endpoints

**Files:**
- Modify: `packages/agent-routing/runtime/model_routing/route_probe.py` `_probe_route` (line 69; branch before the generic `/models` request at line 84) and add `_probe_model_studio`
- Test: `packages/agent-routing/tests/test_route_probe_model_studio.py`

**Interfaces:**
- Consumes: Tasks 3, 7, 8 (`check_key`, `check_model`, `base_url(..., protocol="native")`, `credits_window`, `read_lockout`, `get_subscription_stats`).
- Produces: `ProbeResult` statuses for Model Studio routes: `reachable`, `model-missing`, `unauthorized`, `down`, `misconfigured`, `quota-exhausted`; `detail` names remaining Credits or the renewal date. `doctor` needs no change: it already calls `probe_route` for endpoint routes and treats non-`reachable` as WARN.

- [ ] **Step 1: Write the failing tests** (`packages/agent-routing/tests/test_route_probe_model_studio.py`)

```python
"""routes probe reports Model Studio pairing, reachability, and Credits."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from model_routing import endpoint_slots, model_studio, model_studio_openapi, route_probe, routes  # noqa: E402
from tests.http_test_support import LoopbackServer  # noqa: E402

ENDPOINT = model_studio.validate_endpoint(
    {
        "kind": "model-studio",
        "plan": "token-plan-personal",
        "tier": "pro",
        "apiKeyEnv": "MODEL_STUDIO_API_KEY",
        "renewsOn": "2026-09-12",
        "tokenPlanAutomation": "accept",
    },
    "e",
)
NOW = datetime(2026, 9, 26, tzinfo=timezone.utc)


def entry() -> dict[str, object]:
    return {
        "model": "qwen3.8-flash",
        "endpoint": routes.EndpointReference("ms", ENDPOINT),
        "args": [],
        "env": {},
    }


class ProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {
            "MODEL_STUDIO_API_KEY": "sk-sp-x",
            "SUBAGENT_MODEL_ROUTING_STATE_HOME": self.tmp.name,
        }

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def probe(self, server: LoopbackServer) -> route_probe.ProbeResult:
        with mock.patch.object(
            model_studio,
            "base_url",
            side_effect=lambda ep, protocol=None: server.base_url + "/api/v1",
        ):
            return route_probe.probe_route("flash", entry(), env=self.env, now=NOW, timeout=2.0)

    def test_reachable_reports_renewal_without_stats(self) -> None:
        with LoopbackServer(
            {
                "/api/v1/models": (
                    200,
                    {"output": {"models": [{"model": "qwen3.8-flash"}], "total": 1}},
                )
            }
        ) as server:
            result = self.probe(server)
            self.assertEqual("Bearer sk-sp-x", server.requests[0][1].get("Authorization"))
            self.assertIn("model=qwen3.8-flash", server.requests[0][0])
        self.assertEqual("reachable", result.status)
        self.assertIn("renews 2026-10-12", result.detail)

    def test_missing_model(self) -> None:
        with LoopbackServer(
            {"/api/v1/models": (200, {"output": {"models": [], "total": 0}})}
        ) as server:
            self.assertEqual("model-missing", self.probe(server).status)

    def test_wrong_key_is_misconfigured_without_a_request(self) -> None:
        self.env["MODEL_STUDIO_API_KEY"] = "sk-other"
        with LoopbackServer({}) as server:
            result = self.probe(server)
            self.assertEqual([], server.requests)
        self.assertEqual("misconfigured", result.status)
        self.assertIn("key_plan_mismatch", result.detail)

    def test_local_lockout_reports_quota_exhausted(self) -> None:
        endpoint_slots.write_lockout(self.env, "ms", NOW + timedelta(days=2))
        with LoopbackServer(
            {"/api/v1/models": (200, {"output": {"models": [{"model": "qwen3.8-flash"}]}})}
        ) as server:
            self.assertEqual("quota-exhausted", self.probe(server).status)

    def test_stats_report_remaining_credits(self) -> None:
        stats = model_studio_openapi.SubscriptionStats(
            NOW - timedelta(days=5), NOW + timedelta(days=25), 180000.0, 90000.0
        )
        with (
            mock.patch.object(model_studio_openapi, "get_subscription_stats", return_value=stats),
            LoopbackServer(
                {"/api/v1/models": (200, {"output": {"models": [{"model": "qwen3.8-flash"}]}})}
            ) as server,
        ):
            result = self.probe(server)
        self.assertEqual("reachable", result.status)
        self.assertIn("90000 of 180000 Credits", result.detail)


if __name__ == "__main__":
    unittest.main()
```

`LoopbackServer` records `self.path` including the query string but also routes on it (`server.routes.get(self.path, …)` at `tests/http_test_support.py:31`), so a request with `?model=` would 404. Change that lookup to route on the path alone and keep recording the full path:

```python
                status, body = server.routes.get(self.path.split("?", 1)[0], (404, {"error": "not found"}))
```

- [ ] **Step 2: Run to verify failure**

Run: `cd packages/agent-routing && .venv/bin/python -m unittest tests.test_route_probe_model_studio 2>&1 | tail -4`
Expected: FAIL — the generic probe requests `/compatible-mode/v1/models` on the real base URL mock and reports `model-missing` or `down`.

- [ ] **Step 3: Implement** in `route_probe.py`

Add imports: `from urllib.parse import quote`, `from . import endpoint_slots, model_studio, model_studio_openapi`, and `from .routes import resolved_endpoint, EndpointReference` (if `routes` imports `route_probe`, import these inside the function to avoid a cycle). In `_probe_route`, after the `expired` check (line 83) insert:

```python
resolved = resolved_endpoint(entry)
if resolved is not None and resolved.get("kind") == model_studio.KIND:
    return _probe_model_studio(
        name,
        entry,
        resolved,
        env=env,
        now=now or datetime.now(timezone.utc),
        timeout=timeout,
        expires_at=expires_at,
    )
```

and add:

```python
def _probe_model_studio(
    name: str,
    entry: Mapping[str, Any],
    endpoint: Mapping[str, Any],
    *,
    env: Mapping[str, str],
    now: datetime,
    timeout: float,
    expires_at: str | None,
) -> ProbeResult:
    raw = entry.get("endpoint")
    endpoint_name = str(raw) if isinstance(raw, str) else name
    plan = str(endpoint["plan"])
    model = str(entry["model"])
    key = env.get(str(endpoint["apiKeyEnv"]), "")
    try:
        if not key:
            raise model_studio.ModelStudioError("missing_key", f"export {endpoint['apiKeyEnv']}")
        model_studio.check_key(plan, key)
        model_studio.check_model(plan, model)
    except model_studio.ModelStudioError as exc:
        return ProbeResult(name, "misconfigured", None, (), expires_at, None, str(exc))
    url = f"{model_studio.base_url(endpoint, protocol='native').rstrip('/')}/models?model={quote(model)}"
    headers = {
        "Accept": "application/json",
        "User-Agent": ENDPOINT_USER_AGENT,
        "Authorization": f"Bearer {key}",
    }
    try:
        with request.urlopen(
            request.Request(url, headers=headers, method="GET"), timeout=timeout
        ) as response:
            status_code = int(response.status)
            payload = json.loads(response.read(1024 * 1024).decode("utf-8", errors="replace"))
    except error.HTTPError as exc:
        status = "unauthorized" if exc.code in (401, 403) else "down"
        return ProbeResult(
            name,
            status,
            exc.code,
            (),
            expires_at,
            None,
            f"HTTP {exc.code} from the Model Studio models API",
        )
    except (error.URLError, socket.timeout, OSError, ValueError) as exc:
        return ProbeResult(
            name, "down", None, (), expires_at, None, f"Model Studio models API: {exc}"
        )
    listed = tuple(
        str(item.get("model"))
        for item in (payload.get("output") or {}).get("models") or []
        if isinstance(item, Mapping)
    )
    if model not in listed:
        return ProbeResult(
            name,
            "model-missing",
            status_code,
            listed,
            expires_at,
            None,
            f"{model} is not listed for this key",
        )
    locked_until = endpoint_slots.read_lockout(env, endpoint_name, now=now)
    if locked_until is not None:
        return ProbeResult(
            name,
            "quota-exhausted",
            status_code,
            listed,
            expires_at,
            None,
            f"Credits exhausted; locked until {locked_until}",
        )
    detail = "reachable"
    if model_studio.is_token_plan(plan):
        try:
            stats = model_studio_openapi.get_subscription_stats(env, now=now, timeout=timeout)
        except (error.URLError, OSError, ValueError, KeyError) as exc:
            stats = None
            detail = f"reachable; Credits stats unavailable ({type(exc).__name__})"
        if stats is not None:
            if stats.remaining_credits <= 0:
                return ProbeResult(
                    name,
                    "quota-exhausted",
                    status_code,
                    listed,
                    expires_at,
                    None,
                    f"0 Credits remaining; renews {stats.reset_at.date().isoformat()}",
                )
            detail = f"reachable; {stats.remaining_credits:.0f} of {stats.total_credits:.0f} Credits remaining; renews {stats.reset_at.date().isoformat()}"
        elif endpoint.get("renewsOn"):
            detail = f"{detail}; renews {model_studio.credits_window(str(endpoint['renewsOn']), now)[1].date().isoformat()}"
    return ProbeResult(name, "reachable", status_code, listed, expires_at, None, detail)
```

- [ ] **Step 4: Run tests**

```bash
cd packages/agent-routing
.venv/bin/python -m unittest tests.test_route_probe_model_studio tests.test_route_probe tests.test_doctor 2>&1 | tail -3
```

Expected: `OK`.

- [ ] **Step 5: Run the Agent Routing gates**

```bash
cd packages/agent-routing
.venv/bin/ruff check runtime tests tools scripts/pitwall-agent-routing 2>&1 | tail -1
.venv/bin/mypy --python-version 3.14 runtime/model_routing tools scripts/pitwall-agent-routing 2>&1 | tail -1
.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3
```

Expected: `All checks passed!`; `Success: no issues found`; `OK`.

- [ ] **Step 6: Commit**

```bash
git add packages/agent-routing/runtime/model_routing/route_probe.py packages/agent-routing/tests/test_route_probe_model_studio.py packages/agent-routing/tests/http_test_support.py
git commit -m "feat(agent-routing): Model Studio readiness in routes probe and doctor"
```

---

### Task 10: Broker enums, migration 0035, and the pinned lists that name them

**Files:**
- Modify: `src/pitwall/core/enums.py:89-106` (`ProviderType.MODEL_STUDIO`, `ProviderAdapterId.MODEL_STUDIO`)
- Create: `db/migrations/0035_model_studio.sql`
- Modify: `tests/db/test_zero_cost_mode_migration.py:19-21` (0034 is no longer the head)
- Test: `tests/db/test_model_studio_migration.py`

**Interfaces:**
- Produces: `ProviderType.MODEL_STUDIO = "model_studio"`, `ProviderAdapterId.MODEL_STUDIO = "model_studio"`; DB checks admitting them; `provider_quotas.free_type` admitting `subscription-credits` and `pay-as-you-go`.

- [ ] **Step 1: Write the failing tests** (`tests/db/test_model_studio_migration.py`)

```python
"""Migration 0035: Model Studio provider type/adapter and subscription quota windows."""

from __future__ import annotations

import os
from pathlib import Path

import asyncpg
import pytest

from pitwall.core.enums import ProviderAdapterId, ProviderType
from pitwall.db import _register_codecs
from pitwall.migrations import discover_migrations

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = _ROOT / "db/migrations/0035_model_studio.sql"
_PG_URL = os.getenv("PITWALL_TEST_DATABASE_URL", "")


def test_0035_is_the_latest_migration_and_widens_every_check() -> None:
    records = discover_migrations(_ROOT / "db/migrations")
    assert records[-1].version == "0035_model_studio"
    sql = _MIGRATION.read_text(encoding="utf-8")
    for constraint in (
        "providers_provider_type_check",
        "providers_adapter_id_check",
        "provider_quotas_free_type_check",
    ):
        assert constraint in sql
    assert sql.count("'model_studio'") == 2
    assert "'subscription-credits'" in sql and "'pay-as-you-go'" in sql


def test_enums_match_the_migration() -> None:
    assert ProviderType.MODEL_STUDIO.value == "model_studio"
    assert ProviderAdapterId.MODEL_STUDIO.value == "model_studio"


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.skipif(not _PG_URL, reason="PITWALL_TEST_DATABASE_URL not set")
async def test_model_studio_provider_and_credits_quota_insert() -> None:
    pool = await asyncpg.create_pool(_PG_URL, min_size=1, max_size=1, init=_register_codecs)
    assert pool is not None
    provider_id = "prov_0035_model_studio"
    try:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM pitwall.providers WHERE id = $1", provider_id)
            await conn.execute(
                """
                INSERT INTO pitwall.providers (id, name, adapter_id, credential_ref, provider_type, config, priority)
                VALUES ($1, $1, 'model_studio', 'MODEL_STUDIO_API_KEY', 'model_studio', '{}'::jsonb, 50)
                """,
                provider_id,
            )
            await conn.execute(
                """
                INSERT INTO pitwall.provider_quotas (provider_id, pool_key, free_type, tos_verdict)
                VALUES ($1, '', 'subscription-credits', 'caution')
                """,
                provider_id,
            )
    finally:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM pitwall.providers WHERE id = $1", provider_id)
        await pool.close()
```

Update `tests/db/test_zero_cost_mode_migration.py` lines 19–21:

```python
def test_0034_widens_the_cost_mode_check() -> None:
    records = discover_migrations(_ROOT / "db/migrations")
    assert "0034_capabilities_zero_cost_mode" in [record.version for record in records]
```

(keep the remaining assertions of that test unchanged).

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/db/test_model_studio_migration.py -q 2>&1 | tail -5`
Expected: FAIL — `AttributeError: MODEL_STUDIO` and the latest migration is `0034_…`.

- [ ] **Step 3: Implement**

`src/pitwall/core/enums.py`: add `MODEL_STUDIO = "model_studio"` as the last member of both `ProviderType` and `ProviderAdapterId`, and change the `ProviderType` docstring to `"""Provider surfaces supported by Pitwall."""`.

`db/migrations/0035_model_studio.sql`:

```sql
-- Alibaba Cloud Model Studio: provider type/adapter, and quota windows for Token Plan
-- Credits (subscription-credits) and pay-as-you-go month-to-date spend (pay-as-you-go).

ALTER TABLE pitwall.providers DROP CONSTRAINT IF EXISTS providers_provider_type_check;
ALTER TABLE pitwall.providers ADD CONSTRAINT providers_provider_type_check CHECK (
  provider_type IN ('serverless_queue', 'serverless_lb', 'public_endpoint', 'pod_lease', 'openai_gateway', 'model_studio')
);
ALTER TABLE pitwall.providers DROP CONSTRAINT IF EXISTS providers_adapter_id_check;
ALTER TABLE pitwall.providers ADD CONSTRAINT providers_adapter_id_check CHECK (
  adapter_id IN ('runpod', 'vast', 'together', 'lambda_cloud', 'openai_gateway', 'model_studio')
);
ALTER TABLE pitwall.provider_quotas DROP CONSTRAINT IF EXISTS provider_quotas_free_type_check;
ALTER TABLE pitwall.provider_quotas ADD CONSTRAINT provider_quotas_free_type_check CHECK (
  free_type IN ('recurring-daily', 'recurring-monthly', 'recurring-credit', 'recurring-uncapped',
                'one-time-initial', 'keyless', 'discontinued', 'subscription-credits', 'pay-as-you-go')
);
```

- [ ] **Step 4: Run tests, including the integration test against the test database**

```bash
uv run pytest tests/db/test_model_studio_migration.py tests/db/test_zero_cost_mode_migration.py -q 2>&1 | tail -3
make up >/dev/null && make test-int 2>&1 | tail -5
```

Expected: unit `3 passed` (integration skipped without the URL); `make test-int` finishes with `0 failed` and runs `test_model_studio_provider_and_credits_quota_insert`.

- [ ] **Step 5: Commit**

```bash
git add src/pitwall/core/enums.py db/migrations/0035_model_studio.sql tests/db/test_model_studio_migration.py tests/db/test_zero_cost_mode_migration.py
git commit -m "feat(model-studio): provider type, adapter id, and quota windows in migration 0035"
```

---

### Task 11: Broker catalog module (rules, derivation, pricing, classification)

**Files:**
- Create: `src/pitwall/providers/model_studio/catalog.py`
- Test: `tests/providers/test_model_studio_catalog.py`

**Interfaces:**
- Produces (used by Tasks 12–16):
  - `AUTOMATION_ENV = "MODEL_STUDIO_TOKEN_PLAN_AUTOMATION"`, `class ModelStudioConfigError(ValueError)` with `.code`
  - `load_catalog() -> Mapping[str, Any]`
  - `provider_settings(config: Mapping[str, Any]) -> dict[str, Any]` — validates `config["model_studio"]`: keys `plan`, `tier`, `region`, `workspace`, `model`, `protocol`, `renews_on`, `automation`; returns the normalized mapping
  - `is_token_plan(plan: str) -> bool`
  - `base_url(settings: Mapping[str, Any], protocol: str = "openai") -> str`
  - `check_key(plan: str, key: str) -> None`
  - `check_model(plan: str, model: str) -> Mapping[str, Any]`
  - `automation_accepted(settings: Mapping[str, Any], environ: Mapping[str, str]) -> bool`
  - `require_automation(settings, environ) -> None` (raises `automation_not_accepted`)
  - `pricing_config(settings: Mapping[str, Any]) -> dict[str, Any]` (tagged cost for `config["cost"]`)
  - `classify_error(status: int, body: str) -> tuple[str, int | None] | None`
  - `credits_window(renews_on: str, now: dt.datetime) -> tuple[dt.datetime, dt.datetime]`
  - `next_renewal(settings, now) -> dt.datetime | None`

- [ ] **Step 1: Write the failing tests** (`tests/providers/test_model_studio_catalog.py`)

```python
"""Broker-side Model Studio catalog rules mirror Agent Routing's."""

from __future__ import annotations

import datetime as dt

import pytest

from pitwall.providers.model_studio import catalog as ms

NOW = dt.datetime(2026, 11, 20, 9, 0, tzinfo=dt.UTC)


def settings(**changes: object) -> dict[str, object]:
    base: dict[str, object] = {
        "plan": "token-plan-personal",
        "tier": "pro",
        "model": "qwen3.8-flash",
    }
    base.update(changes)
    return {"model_studio": base}


def test_token_plan_settings_normalize_and_derive_the_base_url() -> None:
    normalized = ms.provider_settings(settings())
    assert normalized["region"] == "ap-southeast-1"
    assert (
        ms.base_url(normalized)
        == "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
    )
    assert ms.base_url(normalized, "native").endswith("/api/v1")


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"region": "us-east-1"}, "region_not_allowed"),
        ({"model": "kimi-k2.7-code"}, "model_not_eligible"),
        ({"model": "wan2.7-image"}, "model_not_text"),
        ({"tier": None}, "invalid_config"),
        ({"plan": "pay-as-you-go", "tier": None}, "unknown_region"),
        ({"plan": "pay-as-you-go", "tier": None, "region": "eu-central-1"}, "workspace_required"),
        ({"renews_on": "12"}, "invalid_config"),
    ],
)
def test_invalid_settings_are_refused_by_name(changes: dict[str, object], code: str) -> None:
    with pytest.raises(ms.ModelStudioConfigError) as caught:
        ms.provider_settings(settings(**changes))
    assert caught.value.code == code


def test_key_pairing_never_echoes_the_key() -> None:
    ms.check_key("token-plan-personal", "sk-sp-ok")
    with pytest.raises(ms.ModelStudioConfigError) as caught:
        ms.check_key("token-plan-personal", "sk-leakcheck")
    assert caught.value.code == "key_plan_mismatch"
    assert "leakcheck" not in str(caught.value)


def test_automation_from_config_or_environment() -> None:
    plain = ms.provider_settings(settings())
    assert not ms.automation_accepted(plain, {})
    assert ms.automation_accepted(plain, {ms.AUTOMATION_ENV: "accept"})
    assert ms.automation_accepted(ms.provider_settings(settings(automation="accept")), {})
    with pytest.raises(ms.ModelStudioConfigError) as caught:
        ms.require_automation(plain, {})
    assert caught.value.code == "automation_not_accepted"
    assert ms.AUTOMATION_ENV in str(caught.value)
    ms.require_automation(
        ms.provider_settings(settings(plan="pay-as-you-go", tier=None, region="ap-southeast-1")), {}
    )


def test_pricing_config_zero_for_token_plan_and_tiered_for_pay_as_you_go() -> None:
    assert ms.pricing_config(ms.provider_settings(settings())) == {"kind": "zero"}
    payg = ms.pricing_config(
        ms.provider_settings(
            settings(plan="pay-as-you-go", tier=None, region="ap-southeast-1", model="qwen3.7-plus")
        )
    )
    assert payg == {
        "kind": "per_token",
        "per_million_input_tokens": "0.4",
        "per_million_output_tokens": "1.6",
        "per_million_cached_input_tokens": "0.08",
        "input_tiers": [
            {
                "above_input_tokens": 256000,
                "per_million_input_tokens": "1.2",
                "per_million_output_tokens": "4.8",
                "per_million_cached_input_tokens": "0.24",
            }
        ],
        "default_max_output_tokens": 131072,
    }


def test_unpriced_region_or_model_needs_operator_cost() -> None:
    with pytest.raises(ms.ModelStudioConfigError) as caught:
        ms.pricing_config(
            ms.provider_settings(settings(plan="pay-as-you-go", tier=None, region="us-east-1"))
        )
    assert caught.value.code == "unpriced"


def test_classification_and_renewal_match_agent_routing() -> None:
    assert ms.classify_error(
        429,
        '{"error": {"code": "insufficient_quota", "message": "Your token-plan quota has been exhausted."}}',
    ) == ("credits_exhausted", None)
    assert ms.classify_error(429, '{"code": "Throttling.RateQuota"}') == ("rate_limit", 60)
    start, reset = ms.credits_window("2026-09-12", NOW)
    assert (start, reset) == (
        dt.datetime(2026, 11, 11, tzinfo=dt.UTC),
        dt.datetime(2026, 12, 11, tzinfo=dt.UTC),
    )
    assert ms.next_renewal(ms.provider_settings(settings(renews_on="2026-09-12")), NOW) == reset
    assert ms.next_renewal(ms.provider_settings(settings()), NOW) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/providers/test_model_studio_catalog.py -q 2>&1 | tail -3`
Expected: `ModuleNotFoundError: No module named 'pitwall.providers.model_studio.catalog'`.

- [ ] **Step 3: Implement** (`src/pitwall/providers/model_studio/catalog.py`)

```python
"""Model Studio catalog rules for the broker; mirrors Agent Routing's model_studio module."""

from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Mapping
from functools import lru_cache
from importlib import resources
from typing import Any

AUTOMATION_ENV = "MODEL_STUDIO_TOKEN_PLAN_AUTOMATION"
AUTOMATION_ACCEPT = "accept"
_SETTINGS_KEYS = frozenset(
    {"plan", "tier", "region", "workspace", "model", "protocol", "renews_on", "automation"}
)
_WORKSPACE = re.compile(r"[A-Za-z0-9-]{1,64}")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class ModelStudioConfigError(ValueError):
    """Named configuration refusal; messages never carry key values."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@lru_cache(maxsize=1)
def load_catalog() -> Mapping[str, Any]:
    raw = (
        resources.files("pitwall.providers.model_studio")
        .joinpath("catalog.json")
        .read_text("utf-8")
    )
    catalog: Mapping[str, Any] = json.loads(raw)
    return catalog


def _plan(plan: str) -> Mapping[str, Any]:
    plans = load_catalog()["plans"]
    if plan not in plans:
        raise ModelStudioConfigError(
            "unknown_plan", f"plan must be one of {', '.join(sorted(plans))}"
        )
    record: Mapping[str, Any] = plans[plan]
    return record


def is_token_plan(plan: str) -> bool:
    return bool(_plan(plan)["family"] == "token-plan")


def check_model(plan: str, model: str) -> Mapping[str, Any]:
    models = load_catalog()["models"]
    if model not in models:
        raise ModelStudioConfigError(
            "model_not_in_catalog", f"model {model!r} is not in the Model Studio catalog"
        )
    record: Mapping[str, Any] = models[model]
    edition = _plan(plan)["edition"]
    eligible = (
        record["eligibility"]["payAsYouGo"] if edition is None else record["eligibility"][edition]
    )
    if not eligible:
        raise ModelStudioConfigError(
            "model_not_eligible", f"model {model!r} is not included in {plan}"
        )
    if record["kind"] != "text":
        raise ModelStudioConfigError(
            "model_not_text", f"model {model!r} is a {record['kind']} model"
        )
    return record


def provider_settings(config: Mapping[str, Any]) -> dict[str, Any]:
    raw = config.get("model_studio")
    if not isinstance(raw, Mapping):
        raise ModelStudioConfigError(
            "invalid_config", "provider config needs a model_studio object"
        )
    unknown = set(raw) - _SETTINGS_KEYS
    if unknown:
        raise ModelStudioConfigError(
            "invalid_config", f"model_studio has unknown fields: {', '.join(sorted(unknown))}"
        )
    plan = raw.get("plan")
    if not isinstance(plan, str):
        raise ModelStudioConfigError("unknown_plan", "model_studio.plan is required")
    record = _plan(plan)
    token_plan = record["family"] == "token-plan"
    region = raw.get("region") or (record["regions"][0] if token_plan else None)
    if region not in record["regions"]:
        if token_plan:
            raise ModelStudioConfigError(
                "region_not_allowed", f"Token Plan is served only from {record['regions'][0]}"
            )
        raise ModelStudioConfigError(
            "unknown_region", f"model_studio.region must be one of {', '.join(record['regions'])}"
        )
    settings: dict[str, Any] = {"plan": plan, "region": region, "protocol": "openai"}
    tier = raw.get("tier")
    tiers: Mapping[str, Any] = record.get("tiers") or {}
    if token_plan and tier not in tiers:
        raise ModelStudioConfigError(
            "invalid_config", f"model_studio.tier must be one of {', '.join(tiers)}"
        )
    if tier is not None:
        if tier not in tiers:
            raise ModelStudioConfigError(
                "invalid_config", "model_studio.tier applies only to Token Plan"
            )
        settings["tier"] = tier
    workspace = raw.get("workspace")
    if workspace is not None:
        if token_plan or not isinstance(workspace, str) or _WORKSPACE.fullmatch(workspace) is None:
            raise ModelStudioConfigError(
                "invalid_config",
                "model_studio.workspace must be a workspace id (pay-as-you-go only)",
            )
        settings["workspace"] = workspace
    renews_on = raw.get("renews_on")
    if renews_on is not None:
        if not token_plan or not isinstance(renews_on, str) or _DATE.fullmatch(renews_on) is None:
            raise ModelStudioConfigError(
                "invalid_config",
                "model_studio.renews_on must be a YYYY-MM-DD date (Token Plan only)",
            )
        dt.date.fromisoformat(renews_on)
        settings["renews_on"] = renews_on
    automation = raw.get("automation")
    if automation is not None:
        if automation != AUTOMATION_ACCEPT:
            raise ModelStudioConfigError(
                "invalid_config", "model_studio.automation must be 'accept'"
            )
        settings["automation"] = automation
    model = raw.get("model")
    if not isinstance(model, str) or not model:
        raise ModelStudioConfigError("invalid_config", "model_studio.model is required")
    check_model(plan, model)
    settings["model"] = model
    base_url(settings)
    return settings


def base_url(settings: Mapping[str, Any], protocol: str = "openai") -> str:
    catalog = load_catalog()
    record = _plan(str(settings["plan"]))
    if record["family"] == "token-plan":
        return str(record["baseUrls"][protocol])
    region = catalog["regions"][str(settings["region"])]
    workspace = settings.get("workspace")
    if workspace:
        host = str(region["workspaceHost"]).format(workspace=workspace)
    elif region.get("legacyHost"):
        host = str(region["legacyHost"])
    else:
        raise ModelStudioConfigError(
            "workspace_required",
            f"region {settings['region']} has no shared host; set model_studio.workspace",
        )
    return f"https://{host}{catalog['paths'][protocol]}"


def check_key(plan: str, key: str) -> None:
    prefix = str(load_catalog()["plans"]["token-plan-personal"]["keyPrefix"])
    token_key = key.startswith(prefix)
    if is_token_plan(plan) and not token_key:
        raise ModelStudioConfigError(
            "key_plan_mismatch", f"plan {plan} needs a Token Plan key (prefix {prefix})"
        )
    if token_key and not is_token_plan(plan):
        raise ModelStudioConfigError(
            "key_plan_mismatch",
            f"a Token Plan key (prefix {prefix}) works only with a Token Plan plan",
        )


def automation_accepted(settings: Mapping[str, Any], environ: Mapping[str, str]) -> bool:
    return (
        settings.get("automation") == AUTOMATION_ACCEPT
        or environ.get(AUTOMATION_ENV, "").strip() == AUTOMATION_ACCEPT
    )


def require_automation(settings: Mapping[str, Any], environ: Mapping[str, str]) -> None:
    if is_token_plan(str(settings["plan"])) and not automation_accepted(settings, environ):
        raise ModelStudioConfigError(
            "automation_not_accepted",
            f"the Token Plan terms allow interactive use only; set {AUTOMATION_ENV}=accept "
            "(or model_studio.automation: accept) to accept the risk of broker use",
        )


def pricing_config(settings: Mapping[str, Any]) -> dict[str, Any]:
    if is_token_plan(str(settings["plan"])):
        return {"kind": "zero"}
    catalog = load_catalog()
    model = catalog["models"][str(settings["model"])]
    prices = model.get("prices")
    if not catalog["regions"][str(settings["region"])]["priced"] or not prices:
        raise ModelStudioConfigError(
            "unpriced",
            f"the catalog has no {settings['region']} price for {settings['model']}; set the provider cost explicitly",
        )
    cost: dict[str, Any] = {
        "kind": "per_token",
        "per_million_input_tokens": prices["input"],
        "per_million_output_tokens": prices["output"],
    }
    if prices.get("cachedInput") is not None:
        cost["per_million_cached_input_tokens"] = prices["cachedInput"]
    if prices.get("tiers"):
        cost["input_tiers"] = [
            {
                "above_input_tokens": tier["aboveInputTokens"],
                "per_million_input_tokens": tier["input"],
                "per_million_output_tokens": tier["output"],
                **(
                    {"per_million_cached_input_tokens": tier["cachedInput"]}
                    if tier.get("cachedInput") is not None
                    else {}
                ),
            }
            for tier in prices["tiers"]
        ]
    if model.get("maxOutputTokens"):
        cost["default_max_output_tokens"] = model["maxOutputTokens"]
    return cost


def _code_and_message(body: str) -> tuple[str | None, str]:
    try:
        payload = json.loads(body)
    except ValueError:
        return None, body
    if not isinstance(payload, Mapping):
        return None, body
    nested = payload.get("error")
    source = nested if isinstance(nested, Mapping) else payload
    code = source.get("code")
    message = source.get("message")
    return (str(code) if code else None), (str(message) if message else body)


def classify_error(status: int, body: str) -> tuple[str, int | None] | None:
    code, message = _code_and_message(body)
    entries = load_catalog()["errors"]
    for entry in entries:
        if "code" in entry and entry["status"] == status and entry["code"] == code:
            return str(entry["classification"]), entry["cooldownSeconds"]
    lowered = message.lower()
    for entry in entries:
        if "message" in entry and entry["status"] == status and entry["message"] in lowered:
            return str(entry["classification"]), entry["cooldownSeconds"]
    return None


def credits_window(renews_on: str, now: dt.datetime) -> tuple[dt.datetime, dt.datetime]:
    anchor = dt.datetime.combine(dt.date.fromisoformat(renews_on), dt.time(), tzinfo=dt.UTC)
    cycle = dt.timedelta(days=30)
    start = anchor + ((now - anchor) // cycle) * cycle
    return start, start + cycle


def next_renewal(settings: Mapping[str, Any], now: dt.datetime) -> dt.datetime | None:
    renews_on = settings.get("renews_on")
    return credits_window(str(renews_on), now)[1] if renews_on else None
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/providers/test_model_studio_catalog.py -q 2>&1 | tail -3`
Expected: `14 passed` (7 parametrized cases plus 7 tests).

- [ ] **Step 5: Commit**

```bash
git add src/pitwall/providers/model_studio/catalog.py tests/providers/test_model_studio_catalog.py
git commit -m "feat(model-studio): broker catalog rules, pricing derivation, and classification"
```

---

### Task 12: Per-token pricing with cached input, context tiers, and a default ceiling

**Files:**
- Modify: `src/pitwall/cost/estimator.py` — `PerTokenPricing` (line 282), add `InputPriceTier`, add public `output_token_ceiling`
- Modify: `src/pitwall/cost/usage.py` — `TokenUsage` gains `cached_tokens: int = 0`; `parse_usage_json` / `parse_usage_sse` read `prompt_tokens_details.cached_tokens`
- Modify: `src/pitwall/routing/production.py` `_usage_derived_actual` (line 2347) — pass `cached_tokens`
- Test: `tests/cost/test_per_token_tiers.py`

**Interfaces:**
- Consumes: Task 11 `pricing_config` output shape.
- Produces: `PerTokenPricing(per_million_cached_input_tokens: Decimal | None = None, input_tiers: tuple[InputPriceTier, ...] = (), default_max_output_tokens: int | None = None)`; `output_token_ceiling(payload: Mapping[str, Any], default: int | None = None) -> Decimal`; `TokenUsage.cached_tokens`.

- [ ] **Step 1: Write the failing tests** (`tests/cost/test_per_token_tiers.py`)

```python
"""Per-token pricing: cached input, context tiers, reasoning, and default ceilings."""

from __future__ import annotations

from decimal import Decimal

import pytest

import datetime as dt

from pitwall.core.enums import CapabilityClass, CapabilitySource, CostMode
from pitwall.core.models import Capability
from pitwall.cost.estimator import PerTokenPricing, output_token_ceiling, parse_pricing_model
from pitwall.cost.usage import parse_usage_json

TIERED = {
    "kind": "per_token",
    "per_million_input_tokens": "0.4",
    "per_million_output_tokens": "1.6",
    "per_million_cached_input_tokens": "0.08",
    "input_tiers": [
        {
            "above_input_tokens": 256000,
            "per_million_input_tokens": "1.2",
            "per_million_output_tokens": "4.8",
            "per_million_cached_input_tokens": "0.24",
        }
    ],
    "default_max_output_tokens": 131072,
}


NOW = dt.datetime(2026, 9, 26, tzinfo=dt.UTC)


def _capability() -> Capability:
    return Capability(
        id="cap_ms",
        name="ms.chat",
        version="1.0.0",
        class_=CapabilityClass.LLM,
        cost_mode=CostMode.PER_TOKEN,
        source=CapabilitySource.YAML,
        created_at=NOW,
        updated_at=NOW,
    )


def _pricing() -> PerTokenPricing:
    pricing = parse_pricing_model({"cost": TIERED})
    assert isinstance(pricing, PerTokenPricing)
    return pricing


def test_legacy_pricing_is_unchanged() -> None:
    plain = parse_pricing_model(
        {
            "cost": {
                "kind": "per_token",
                "per_million_input_tokens": "1",
                "per_million_output_tokens": "2",
            }
        }
    )
    assert plain.estimate(
        _capability(), {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}
    ) == Decimal("3")


def test_cached_tokens_use_the_cached_price() -> None:
    cost = _pricing().estimate(
        _capability(),
        {"prompt_tokens": 1_000_000, "cached_tokens": 500_000, "completion_tokens": 0},
    )
    assert cost == Decimal("0.24")  # 500k * 0.4 + 500k * 0.08 per million


def test_tier_applies_above_the_threshold() -> None:
    cost = _pricing().estimate(
        _capability(), {"prompt_tokens": 300_000, "completion_tokens": 1_000_000}
    )
    assert cost == Decimal("5.16")  # 300k * 1.2 + 1M * 4.8 per million


def test_upper_bound_uses_the_highest_tier_and_default_ceiling() -> None:
    bound = _pricing().upper_bound(_capability(), {"prompt_tokens": 1000})
    expected = (Decimal(1000) * Decimal("1.2") + Decimal(131072) * Decimal("4.8")) / Decimal(
        1_000_000
    )
    assert expected <= bound < expected + Decimal("0.00001")


def test_output_ceiling_prefers_the_request_then_the_default() -> None:
    assert output_token_ceiling({"max_tokens": 900}, 131072) == Decimal(900)
    assert output_token_ceiling({}, 131072) == Decimal(131072)
    with pytest.raises(ValueError):
        output_token_ceiling({}, None)


def test_usage_reads_cached_tokens_and_counts_reasoning_inside_completion() -> None:
    usage = parse_usage_json(
        {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "prompt_tokens_details": {"cached_tokens": 40},
                "completion_tokens_details": {"reasoning_tokens": 30},
            }
        }
    )
    assert usage is not None
    assert (usage.prompt_tokens, usage.completion_tokens, usage.cached_tokens) == (100, 50, 40)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/cost/test_per_token_tiers.py -q 2>&1 | tail -5`
Expected: FAIL — `ImportError: cannot import name 'output_token_ceiling'`.

- [ ] **Step 3: Implement** in `src/pitwall/cost/estimator.py`

Add above `PerTokenPricing`:

```python
class InputPriceTier(BaseModel):
    """Rates that apply when a request's input exceeds ``above_input_tokens``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    above_input_tokens: int = Field(gt=0)
    per_million_input_tokens: Decimal
    per_million_output_tokens: Decimal
    per_million_cached_input_tokens: Decimal | None = None

    @field_validator(
        "per_million_input_tokens",
        "per_million_output_tokens",
        "per_million_cached_input_tokens",
        mode="before",
    )
    @classmethod
    def _validate_rates(cls, value: object) -> Decimal | None:
        return None if value is None else _non_negative_decimal(value, "input tier rate")
```

(use the `BaseModel`, `ConfigDict`, `Field`, `field_validator` names already imported by `estimator.py`; add any that are missing.)

Add fields and validators to `PerTokenPricing`:

```python
per_million_cached_input_tokens: Decimal | None = None
input_tiers: tuple[InputPriceTier, ...] = ()
default_max_output_tokens: int | None = Field(default=None, gt=0)


@field_validator("per_million_cached_input_tokens", mode="before")
@classmethod
def _validate_cached_rate(cls, value: object) -> Decimal | None:
    return (
        None if value is None else _non_negative_decimal(value, "per_million_cached_input_tokens")
    )


@field_validator("input_tiers")
@classmethod
def _validate_tiers(cls, value: tuple[InputPriceTier, ...]) -> tuple[InputPriceTier, ...]:
    thresholds = [tier.above_input_tokens for tier in value]
    if thresholds != sorted(set(thresholds)):
        raise ValueError("input_tiers must ascend by above_input_tokens without duplicates")
    return value


def _rates(self, input_tokens: Decimal) -> tuple[Decimal, Decimal, Decimal | None]:
    rates = (
        self.per_million_input_tokens,
        self.per_million_output_tokens,
        self.per_million_cached_input_tokens,
    )
    for tier in self.input_tiers:
        if input_tokens > tier.above_input_tokens:
            rates = (
                tier.per_million_input_tokens,
                tier.per_million_output_tokens,
                tier.per_million_cached_input_tokens,
            )
    return rates


def _ceiling_rates(self) -> tuple[Decimal, Decimal]:
    inputs = [
        self.per_million_input_tokens,
        *(tier.per_million_input_tokens for tier in self.input_tiers),
    ]
    outputs = [
        self.per_million_output_tokens,
        *(tier.per_million_output_tokens for tier in self.input_tiers),
    ]
    return max(inputs), max(outputs)
```

Replace `estimate`, `quote_components`, and `_components`:

```python
def estimate(self, capability: Capability, payload: EstimatePayload) -> Decimal:
    input_tokens, output_tokens = _estimate_tokens(payload, capability)
    input_rate, output_rate, cached_rate = self._rates(input_tokens)
    cached = _cached_token_count(payload, input_tokens) if cached_rate is not None else Decimal(0)
    components = self._components(
        input_tokens - cached,
        input_tokens - cached,
        output_tokens,
        output_tokens,
        rates=(input_rate, output_rate, cached_rate),
        ceiling_rates=(input_rate, output_rate),
        cached_tokens=cached,
    )
    return _component_total(components, ceiling=False)


def quote_components(
    self, capability: Capability, payload: EstimatePayload
) -> tuple[CostComponent, ...]:
    input_tokens, output_tokens = _estimate_tokens(payload, capability)
    input_ceiling = _input_token_upper_bound(payload, input_tokens)
    output_ceiling = output_token_ceiling(payload, self.default_max_output_tokens)
    _validate_count_bound(output_tokens, output_ceiling, "max_output_tokens")
    input_rate, output_rate, _cached = self._rates(input_tokens)
    return self._components(
        input_tokens,
        input_ceiling,
        output_tokens,
        output_ceiling,
        rates=(input_rate, output_rate, None),
        ceiling_rates=self._ceiling_rates(),
        cached_tokens=Decimal(0),
    )


def _components(
    self,
    input_tokens: Decimal,
    input_ceiling: Decimal,
    output_tokens: Decimal,
    output_ceiling: Decimal,
    *,
    rates: tuple[Decimal, Decimal, Decimal | None] | None = None,
    ceiling_rates: tuple[Decimal, Decimal] | None = None,
    cached_tokens: Decimal = Decimal(0),
) -> tuple[CostComponent, ...]:
    base_in, base_out, base_cached = rates or (
        self.per_million_input_tokens,
        self.per_million_output_tokens,
        None,
    )
    ceil_in, ceil_out = ceiling_rates or (base_in, base_out)
    input_rate = base_in / _ONE_MILLION
    output_rate = base_out / _ONE_MILLION
    ceiling_input_rate = ceil_in / _ONE_MILLION
    ceiling_output_rate = ceil_out / _ONE_MILLION
    components: list[CostComponent] = [
        _cost_component(
            "input_tokens",
            "input_token",
            input_rate,
            input_tokens,
            input_ceiling,
            ceiling_rate=ceiling_input_rate,
        )
    ]
    products = [(input_rate, input_tokens), (output_rate, output_tokens)]
    if base_cached is not None and cached_tokens > 0:
        cached_rate = base_cached / _ONE_MILLION
        components.append(
            _cost_component(
                "cached_input_tokens", "input_token", cached_rate, cached_tokens, cached_tokens
            )
        )
        products.append((cached_rate, cached_tokens))
    estimate_total = _sum_products_usd(*products)
    output_estimate = estimate_total - sum(
        (component.estimate for component in components), Decimal(0)
    )
    output_ceiling_amount = _multiply_usd(
        ceiling_output_rate, output_ceiling, rounding=ROUND_CEILING
    )
    components.append(
        CostComponent(
            name="output_tokens",
            unit="output_token",
            rate=output_rate,
            ceiling_rate=ceiling_output_rate,
            estimated_count=output_tokens,
            ceiling_count=output_ceiling,
            # Allocate aggregate micro-dollar rounding to the last component.
            estimate=output_estimate,
            ceiling=max(output_estimate, output_ceiling_amount),
        )
    )
    return tuple(components)
```

Add module-level helpers near `_estimate_output_token_upper_bound` (line 1174):

```python
_MAX_OUTPUT_KEYS = ("max_tokens", "max_output_tokens", "max_completion_tokens", "max_new_tokens")


def output_token_ceiling(payload: Mapping[str, Any], default: int | None = None) -> Decimal:
    """The admitted completion ceiling: the request's max-token field, else ``default``."""
    if default is not None and _first_present(payload, *_MAX_OUTPUT_KEYS) is _MISSING:
        return Decimal(default)
    return _estimate_output_token_upper_bound(payload)


def _cached_token_count(payload: EstimatePayload, input_tokens: Decimal) -> Decimal:
    raw = _token_count(payload, "cached_tokens")
    if raw is _MISSING:
        return Decimal(0)
    return min(_non_negative_decimal(raw, "cached_tokens"), input_tokens)
```

`_estimate_output_token_count` (line 1157) keeps estimating 256 output tokens when no max-token field is present; the default only changes the ceiling. Export `InputPriceTier` and `output_token_ceiling` in the module's `__all__` if it has one.

In `src/pitwall/cost/usage.py`, add `cached_tokens: int = 0` as the last `TokenUsage` field and set it in both parsers from `usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)` when it is a non-negative int (ignore any other type).

In `routing/production.py` `_usage_derived_actual`, pass the cached count:

```python
        return candidate.quote.pricing.estimate(
            candidate.quote.capability,
            {
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "cached_tokens": usage.cached_tokens,
            },
        )
```

- [ ] **Step 4: Run tests, including the whole cost and routing suites**

```bash
uv run pytest tests/cost/test_per_token_tiers.py -q 2>&1 | tail -3
uv run pytest tests/cost tests/routing tests/providers -q 2>&1 | tail -3
```

Expected: new tests pass; the suites end `0 failed`. A changed component list in an existing quote snapshot test is a regression: legacy pricing (no cached rate, no tiers) must produce exactly the old two components.

- [ ] **Step 5: Commit**

```bash
git add src/pitwall/cost/estimator.py src/pitwall/cost/usage.py src/pitwall/routing/production.py tests/cost/test_per_token_tiers.py
git commit -m "feat(cost): per-token cached-input and context-tier pricing with a default output ceiling"
```

---

### Task 13: `ModelStudioProvider` adapter, lockout wiring, and registry

**Files:**
- Create: `src/pitwall/providers/model_studio/adapter.py`
- Modify: `src/pitwall/providers/model_studio/__init__.py` (exports)
- Modify: `src/pitwall/providers/registry.py` `create_default_registry` (register after `GatewayProvider`)
- Modify: `src/pitwall/providers/__init__.py` (lazy export map, line ~101; `__all__`)
- Modify: `src/pitwall/providers/gateway.py:46` (`QuotaReason` gains `"billing_state"`)
- Modify: `src/pitwall/routing/lockout.py` `record_failure` (line 120) and `model_lockout_key` (line 219)
- Modify: `tests/providers/test_core_contracts.py:231-255` (registry id lists)
- Modify: `tests/conftest.py:54-72` (live gate env and hosts), `tests/test_current_provider_live_guard.py:15`
- Test: `tests/providers/test_model_studio_provider.py`, `tests/routing/test_lockout_reset.py`

**Interfaces:**
- Consumes: Task 11 (`provider_settings`, `require_automation`, `check_key`, `check_model`, `base_url`, `classify_error`, `next_renewal`); Task 12 `output_token_ceiling`; `QuotaExhausted`.
- Produces: `ModelStudioProvider(id="model_studio", capabilities={SYNC_INFERENCE, AVAILABILITY})`, `ModelStudioCredentials(api_key, timeout_s=600)`, `ModelStudioInferenceResult`, `ModelStudioProviderError`; `build_chat_body(payload, settings, record) -> dict`; lockout keys for Model Studio providers.

- [ ] **Step 1: Write the lockout tests** (`tests/routing/test_lockout_reset.py`)

```python
"""Explicit reset times win over exponential backoff for every non-permanent reason."""

from __future__ import annotations

import datetime as dt

from pitwall.routing.lockout import LockoutKey, LockoutTable, model_lockout_key

NOW = dt.datetime(2026, 9, 26, 12, 0, tzinfo=dt.UTC)
KEY = LockoutKey("prov", "qwen3.8-flash")


def test_rate_limit_with_reset_uses_the_reset() -> None:
    state = LockoutTable().record_failure(
        KEY, now=NOW, reason="rate_limit_exceeded", reset_at=NOW + dt.timedelta(seconds=60)
    )
    assert state.locked_until == NOW + dt.timedelta(seconds=60)


def test_rate_limit_without_reset_keeps_backoff() -> None:
    state = LockoutTable().record_failure(KEY, now=NOW, reason="rate_limit_exceeded")
    assert state.locked_until == NOW + dt.timedelta(seconds=120)


def test_model_studio_providers_have_a_lockout_key() -> None:
    provider = {"id": "prov", "config": {"model_studio": {"model": "qwen3.8-flash"}}}
    assert model_lockout_key(provider) == KEY
```

(`LockoutTable()` must be constructible without arguments; if its `__init__` requires arguments, pass the ones `get_lockout_table()` uses.)

- [ ] **Step 2: Write the adapter tests** (`tests/providers/test_model_studio_provider.py`)

```python
"""ModelStudioProvider: streaming, usage, ceilings, gates, and 429 classification."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import httpx
import pytest

from pitwall.core.enums import ProviderAdapterId, ProviderType
from pitwall.core.models import Provider as ProviderRecord
from pitwall.providers.gateway import QuotaExhausted
from pitwall.providers.interface import CredentialReference
from pitwall.providers.model_studio.adapter import (
    ModelStudioProvider,
    ModelStudioProviderError,
    build_chat_body,
)
from pitwall.providers.model_studio.catalog import (
    ModelStudioConfigError,
    check_model,
    provider_settings,
)

NOW = dt.datetime(2026, 9, 26, 12, 0, tzinfo=dt.UTC)
ACCEPT = {"MODEL_STUDIO_TOKEN_PLAN_AUTOMATION": "accept"}


@pytest.fixture(autouse=True)
def _key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_STUDIO_API_KEY", "sk-sp-test")


def _record(**model_studio: Any) -> ProviderRecord:
    settings = {
        "plan": "token-plan-personal",
        "tier": "pro",
        "model": "qwen3.8-flash",
        "renews_on": "2026-09-12",
        **model_studio,
    }
    return ProviderRecord(
        id="prov_ms",
        capability_id="cap_ms",
        name="ms",
        adapter_id=ProviderAdapterId.MODEL_STUDIO,
        credential_ref="MODEL_STUDIO_API_KEY",
        provider_type=ProviderType.MODEL_STUDIO,
        config={"model_studio": settings, "cost": {"kind": "zero"}},
    )


def _sse(*chunks: dict[str, Any]) -> bytes:
    return (
        b"".join(f"data: {json.dumps(chunk)}\n\n".encode() for chunk in chunks)
        + b"data: [DONE]\n\n"
    )


def _provider(handler: Any, environ: dict[str, str] | None = None) -> ModelStudioProvider:
    return ModelStudioProvider(
        transport=httpx.MockTransport(handler),
        environ=ACCEPT if environ is None else environ,
        now=lambda: NOW,
    )


async def _infer(
    provider: ModelStudioProvider, payload: dict[str, Any], record: ProviderRecord | None = None
) -> Any:
    return await provider.infer(
        credentials=CredentialReference(name="MODEL_STUDIO_API_KEY"),
        provider_record=record or _record(),
        payload=payload,
    )


@pytest.mark.anyio
async def test_streams_with_usage_and_sends_the_ceiling_as_max_completion_tokens() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            content=_sse(
                {
                    "model": "qwen3.8-flash",
                    "choices": [{"index": 0, "delta": {"reasoning_content": "think"}}],
                },
                {"model": "qwen3.8-flash", "choices": [{"index": 0, "delta": {"content": "Hel"}}]},
                {
                    "model": "qwen3.8-flash",
                    "choices": [{"index": 0, "delta": {"content": "lo"}, "finish_reason": "stop"}],
                },
                {
                    "model": "qwen3.8-flash",
                    "choices": [],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 7,
                        "total_tokens": 17,
                        "completion_tokens_details": {"reasoning_tokens": 5},
                    },
                },
            ),
            headers={"content-type": "text/event-stream"},
        )

    result = await _infer(
        _provider(handler), {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 900}
    )
    assert (
        seen["url"]
        == "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1/chat/completions"
    )
    assert seen["auth"] == "Bearer sk-sp-test"
    assert seen["body"]["stream"] is True and seen["body"]["stream_options"] == {
        "include_usage": True
    }
    assert seen["body"]["max_completion_tokens"] == 900 and "max_tokens" not in seen["body"]
    assert (
        result.content,
        result.prompt_tokens,
        result.completion_tokens,
        result.total_tokens,
    ) == ("Hello", 10, 7, 17)
    assert result.raw["usage"]["prompt_tokens"] == 10


@pytest.mark.anyio
async def test_stream_without_usage_reports_unavailable_usage() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_sse(
                {"choices": [{"index": 0, "delta": {"content": "x"}, "finish_reason": "stop"}]}
            ),
        )

    result = await _infer(_provider(handler), {"messages": [], "max_tokens": 10})
    assert result.prompt_tokens is None and "usage" not in result.raw


def test_models_whose_max_tokens_include_reasoning_keep_max_tokens() -> None:
    settings = provider_settings(
        {
            "model_studio": {
                "plan": "token-plan-personal",
                "tier": "pro",
                "model": "deepseek-v4.1-flash",
            }
        }
    )
    body = build_chat_body(
        {"messages": []}, settings, check_model("token-plan-personal", "deepseek-v4.1-flash")
    )
    assert body["max_tokens"] == 393216 and "max_completion_tokens" not in body


def test_reasoning_effort_with_thinking_budget_is_refused_on_qwen38() -> None:
    settings = provider_settings(
        {"model_studio": {"plan": "token-plan-personal", "tier": "pro", "model": "qwen3.8-max"}}
    )
    record = check_model("token-plan-personal", "qwen3.8-max")
    with pytest.raises(ModelStudioConfigError, match="thinking_budget"):
        build_chat_body(
            {"messages": [], "reasoning_effort": "low", "thinking_budget": 100}, settings, record
        )
    with pytest.raises(ModelStudioConfigError, match="xhigh, medium, low"):
        build_chat_body({"messages": [], "reasoning_effort": "max"}, settings, record)


@pytest.mark.anyio
async def test_automation_gate_refuses_before_any_request() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request may leave without acceptance")

    with pytest.raises(ModelStudioConfigError, match="automation_not_accepted"):
        await _infer(_provider(handler, {}), {"messages": [], "max_tokens": 5})


@pytest.mark.anyio
async def test_pay_as_you_go_key_on_token_plan_is_refused_without_echo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_STUDIO_API_KEY", "sk-leaky")

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request")

    with pytest.raises(ModelStudioConfigError) as caught:
        await _infer(_provider(handler), {"messages": [], "max_tokens": 5})
    assert caught.value.code == "key_plan_mismatch" and "leaky" not in str(caught.value)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "body", "reason", "reset"),
    [
        (
            429,
            {"error": {"code": "insufficient_quota", "message": "Allocated quota exceeded"}},
            "rate_limit_exceeded",
            NOW + dt.timedelta(seconds=60),
        ),
        (
            429,
            {"code": "Throttling.RateQuota", "message": "Requests rate limit exceeded"},
            "rate_limit_exceeded",
            NOW + dt.timedelta(seconds=60),
        ),
        (
            429,
            {
                "error": {
                    "code": "insufficient_quota",
                    "message": "Your token-plan quota has been exhausted.",
                }
            },
            "quota_exhausted",
            dt.datetime(2026, 10, 12, tzinfo=dt.UTC),
        ),
        (
            400,
            {
                "code": "Arrearage",
                "message": "Access denied, please make sure your account is in good standing.",
            },
            "billing_state",
            NOW + dt.timedelta(hours=1),
        ),
    ],
)
async def test_errors_map_to_typed_lockouts(
    status: int, body: dict[str, Any], reason: str, reset: dt.datetime
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    with pytest.raises(QuotaExhausted) as caught:
        await _infer(_provider(handler), {"messages": [], "max_tokens": 5})
    assert (caught.value.reason, caught.value.reset_at) == (reason, reset)


@pytest.mark.anyio
async def test_401_is_an_endpoint_pairing_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"code": "InvalidApiKey", "message": "Invalid API-key provided."}
        )

    with pytest.raises(ModelStudioProviderError, match="invalid_endpoint_pairing"):
        await _infer(_provider(handler), {"messages": [], "max_tokens": 5})
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/providers/test_model_studio_provider.py tests/routing/test_lockout_reset.py -q 2>&1 | tail -5`
Expected: `ModuleNotFoundError: … model_studio.adapter` and lockout assertion failures.

- [ ] **Step 4: Implement lockouts** in `routing/lockout.py` `record_failure`, replacing the `elif reason == "quota_exhausted" and reset_at is not None:` branch with:

```python
            elif reset_at is not None:
                state = LockoutState(failures=failures, locked_until=reset_at, reason=reason)
```

and in `model_lockout_key`, replace the `gateway` lookup with:

```python
    gateway = config.get("gateway")
    model_id = gateway.get("model_id") if isinstance(gateway, Mapping) else None
    if model_id is None:
        model_studio = config.get("model_studio")
        model_id = model_studio.get("model") if isinstance(model_studio, Mapping) else None
```

(keep the existing type check on `model_id` and the provider-id logic below it). In `providers/gateway.py` line 46, add `"billing_state"` to the `QuotaReason` literal.

- [ ] **Step 5: Implement the adapter** (`src/pitwall/providers/model_studio/adapter.py`)

```python
"""Alibaba Cloud Model Studio provider adapter (OpenAI-compatible, always streaming)."""

from __future__ import annotations

import datetime as dt
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from pitwall.core.models import Provider as ProviderRecord
from pitwall.cost.estimator import output_token_ceiling
from pitwall.providers.gateway import QuotaExhausted
from pitwall.providers.interface import (
    AvailabilityItem,
    AvailabilityKind,
    AvailabilityRequest,
    AvailabilityResult,
    CredentialInput,
    InferenceRequest,
    InferenceResult,
    ProviderCapability,
    resolve_adapter_credentials,
)
from pitwall.providers.model_studio import catalog

_MAX_ERROR_BODY_CHARS = 500
_EXCLUSIVE = "exclusiveWith"


class ModelStudioCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    api_key: SecretStr = Field(min_length=1)
    timeout_s: float = Field(default=600.0, gt=0)


class ModelStudioProviderError(RuntimeError):
    def __init__(self, status_code: int, classification: str, body: str) -> None:
        self.status_code = status_code
        self.classification = classification
        super().__init__(
            f"Model Studio request failed with HTTP {status_code} ({classification}): {body[:_MAX_ERROR_BODY_CHARS]}"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelStudioInferenceResult(InferenceResult):
    content: str | None
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    finish_reason: str | None
    raw: Mapping[str, Any] = field(default_factory=dict)


def build_chat_body(
    payload: Mapping[str, Any], settings: Mapping[str, Any], record: Mapping[str, Any]
) -> dict[str, Any]:
    body = dict(payload)
    model = str(settings["model"])
    requested = body.get("model")
    if requested not in (None, model):
        raise catalog.ModelStudioConfigError(
            "model_mismatch", f"payload model must be {model!r} for this provider"
        )
    body["model"] = model
    effort = record.get("effort")
    if "reasoning_effort" in body:
        if not effort:
            raise catalog.ModelStudioConfigError(
                "invalid_thinking_parameters", f"{model} has no reasoning_effort control"
            )
        if body["reasoning_effort"] not in effort["values"]:
            raise catalog.ModelStudioConfigError(
                "invalid_thinking_parameters",
                f"reasoning_effort must be one of {', '.join(effort['values'])} for {model}",
            )
        for other in effort.get(_EXCLUSIVE, []):
            if other in body:
                raise catalog.ModelStudioConfigError(
                    "invalid_thinking_parameters",
                    f"reasoning_effort cannot be combined with {other} on {model}",
                )
    ceiling = int(output_token_ceiling(payload, record.get("maxOutputTokens")))
    for key in ("max_tokens", "max_output_tokens", "max_completion_tokens", "max_new_tokens"):
        body.pop(key, None)
    body["max_tokens" if record.get("maxTokensIncludesReasoning") else "max_completion_tokens"] = (
        ceiling
    )
    body["stream"] = True
    options = body.get("stream_options")
    body["stream_options"] = {
        **(options if isinstance(options, Mapping) else {}),
        "include_usage": True,
    }
    return body


class ModelStudioProvider:
    id = "model_studio"
    name = "Alibaba Cloud Model Studio"
    credential_schema = ModelStudioCredentials
    capabilities = frozenset({ProviderCapability.SYNC_INFERENCE, ProviderCapability.AVAILABILITY})

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        environ: Mapping[str, str] | None = None,
        now: Callable[[], dt.datetime] | None = None,
    ) -> None:
        self._transport = transport
        self._environ = environ
        self._now = now or (lambda: dt.datetime.now(dt.UTC))

    def _env(self) -> Mapping[str, str]:
        return os.environ if self._environ is None else self._environ

    async def infer(
        self,
        request: InferenceRequest | None = None,
        *,
        credentials: CredentialInput | None = None,
        provider_record: ProviderRecord | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> ModelStudioInferenceResult:
        if request is not None:
            credentials, provider_record, payload = (
                request.credentials,
                request.provider_record,
                request.payload,
            )
        if credentials is None or provider_record is None or payload is None:
            raise TypeError("infer requires an InferenceRequest or all compatibility arguments")
        settings = catalog.provider_settings(provider_record.config)
        catalog.require_automation(settings, self._env())
        resolved = resolve_adapter_credentials(
            credentials, ModelStudioCredentials, adapter_id=self.id
        )
        key = resolved.api_key.get_secret_value()
        catalog.check_key(str(settings["plan"]), key)
        record = catalog.check_model(str(settings["plan"]), str(settings["model"]))
        body = build_chat_body(payload, settings, record)
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        async with httpx.AsyncClient(
            base_url=catalog.base_url(settings),
            timeout=resolved.timeout_s,
            transport=self._transport,
        ) as client:
            async with client.stream(
                "POST", "chat/completions", headers=headers, json=body
            ) as response:
                if response.status_code >= 400:
                    text = (await response.aread()).decode("utf-8", errors="replace")
                    raise self._error(response.status_code, text, settings)
                return await _collect_stream(
                    response, provider_id=provider_record.id, fallback_model=str(settings["model"])
                )

    def _error(self, status: int, body: str, settings: Mapping[str, Any]) -> Exception:
        classified = catalog.classify_error(status, body)
        now = self._now()
        if classified is None:
            return ModelStudioProviderError(status, "unclassified", body)
        classification, cooldown = classified
        if classification == "rate_limit":
            return QuotaExhausted(
                status,
                body,
                reason="rate_limit_exceeded",
                reset_at=now + dt.timedelta(seconds=cooldown or 60),
            )
        if classification == "credits_exhausted":
            return QuotaExhausted(
                status, body, reason="quota_exhausted", reset_at=catalog.next_renewal(settings, now)
            )
        if classification == "billing_state":
            return QuotaExhausted(
                status,
                body,
                reason="billing_state",
                reset_at=now + dt.timedelta(seconds=cooldown or 3600),
            )
        if classification == "unavailable":
            return QuotaExhausted(
                status,
                body,
                reason="model_capacity",
                reset_at=now + dt.timedelta(seconds=cooldown or 60),
            )
        return ModelStudioProviderError(status, classification, body)

    async def availability(self, request: AvailabilityRequest) -> AvailabilityResult:
        settings = catalog.provider_settings(request.provider_record.config)
        resolved = resolve_adapter_credentials(
            request.credentials, ModelStudioCredentials, adapter_id=self.id
        )
        key = resolved.api_key.get_secret_value()
        catalog.check_key(str(settings["plan"]), key)
        async with httpx.AsyncClient(
            base_url=catalog.base_url(settings, "native"),
            timeout=resolved.timeout_s,
            transport=self._transport,
        ) as client:
            response = await client.get(
                "models",
                params={"model": settings["model"]},
                headers={"Authorization": f"Bearer {key}"},
            )
        if response.status_code >= 400:
            raise self._error(response.status_code, response.text, settings)
        models = (response.json().get("output") or {}).get("models") or []
        items = tuple(
            AvailabilityItem(
                provider_id=request.provider_record.id,
                resource_id=str(item["model"]),
                kind=AvailabilityKind.MODEL,
                available=True,
                pricing={},
                attributes={"context_window": (item.get("model_info") or {}).get("context_window")},
            )
            for item in models
            if isinstance(item, Mapping) and item.get("model") == settings["model"]
        )
        observed = request.context.now or self._now()
        return AvailabilityResult(
            provider_id=request.provider_record.id,
            observed_at=observed.astimezone(dt.UTC),
            source_contract="model-studio-api-v1-models-2026-09-26",
            items=items[: request.limit],
        )


async def _collect_stream(
    response: httpx.Response, *, provider_id: str, fallback_model: str
) -> ModelStudioInferenceResult:
    content: list[str] = []
    reasoning: list[str] = []
    tool_calls: dict[int, dict[str, Any]] = {}
    finish_reason: str | None = None
    model = fallback_model
    usage: Mapping[str, Any] | None = None
    async for line in response.aiter_lines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        chunk = json.loads(data)
        model = str(chunk.get("model") or model)
        if isinstance(chunk.get("usage"), Mapping):
            usage = chunk["usage"]
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            if delta.get("content"):
                content.append(str(delta["content"]))
            if delta.get("reasoning_content"):
                reasoning.append(str(delta["reasoning_content"]))
            for call in delta.get("tool_calls") or []:
                slot = tool_calls.setdefault(
                    int(call.get("index", 0)),
                    {"id": None, "type": "function", "function": {"name": "", "arguments": ""}},
                )
                slot["id"] = call.get("id") or slot["id"]
                function = call.get("function") or {}
                slot["function"]["name"] += function.get("name") or ""
                slot["function"]["arguments"] += function.get("arguments") or ""
            finish_reason = choice.get("finish_reason") or finish_reason
    text = "".join(content) or None
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if reasoning:
        message["reasoning_content"] = "".join(reasoning)
    if tool_calls:
        message["tool_calls"] = [tool_calls[index] for index in sorted(tool_calls)]
    raw: dict[str, Any] = {
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
    }
    prompt = completion = total = None
    if usage is not None:
        raw["usage"] = dict(usage)
        prompt = _count(usage.get("prompt_tokens"))
        completion = _count(usage.get("completion_tokens"))
        total = _count(usage.get("total_tokens"))
        if total is None and prompt is not None and completion is not None:
            total = prompt + completion
    output = {
        "content": text,
        "model": model,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "finish_reason": finish_reason,
    }
    return ModelStudioInferenceResult(
        provider_id=provider_id,
        output=output,
        content=text,
        model=model,
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        finish_reason=finish_reason,
        raw=raw,
    )


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
```

`resolve_adapter_credentials` reads the key named by the `CredentialReference` from `os.environ`; `environ` on the adapter governs only the automation setting.

`src/pitwall/providers/model_studio/__init__.py`:

```python
"""Alibaba Cloud Model Studio provider."""

from pitwall.providers.model_studio.adapter import (
    ModelStudioCredentials,
    ModelStudioInferenceResult,
    ModelStudioProvider,
    ModelStudioProviderError,
)

__all__ = [
    "ModelStudioCredentials",
    "ModelStudioInferenceResult",
    "ModelStudioProvider",
    "ModelStudioProviderError",
]
```

Register in `providers/registry.py` `create_default_registry()` after the gateway registration (`registry.register(ModelStudioProvider())`, following the exact call style used for the others), add the `"ModelStudioProvider": ("pitwall.providers.model_studio", "ModelStudioProvider")` entry to the lazy map in `providers/__init__.py`, and add it to `__all__` there.

Update `tests/providers/test_core_contracts.py` lists: `registry.ids` ends with `"openai_gateway", "model_studio"`; `SYNC_INFERENCE` ids end with `"openai_gateway", "model_studio"`; `AVAILABILITY` ids end with `"openai_gateway", "model_studio"`.

In `tests/conftest.py` add `"model_studio": ("PITWALL_MODEL_STUDIO_LIVE",),` to `CURRENT_PROVIDER_LIVE_ENV_VARS` and these hosts to `_CURRENT_PROVIDER_CONTROL_PLANE_HOSTS`:

```python
    "token-plan.ap-southeast-1.maas.aliyuncs.com": "model_studio",
    "modelstudio.ap-southeast-1.aliyuncs.com": "model_studio",
    "dashscope-intl.aliyuncs.com": "model_studio",
```

and add `"model_studio": "token-plan.ap-southeast-1.maas.aliyuncs.com",` to the map in `tests/test_current_provider_live_guard.py:15`.

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/providers/test_model_studio_provider.py tests/routing/test_lockout_reset.py tests/providers/test_core_contracts.py tests/test_current_provider_live_guard.py -q 2>&1 | tail -3
uv run pytest tests/providers tests/routing -q 2>&1 | tail -3
```

Expected: all pass; suites `0 failed`.

- [ ] **Step 7: Commit**

```bash
git add src/pitwall/providers src/pitwall/routing/lockout.py tests/providers tests/routing/test_lockout_reset.py tests/conftest.py tests/test_current_provider_live_guard.py
git commit -m "feat(model-studio): streaming provider adapter with typed lockouts and gates"
```

---

### Task 14: Provider configuration — seed, API validation, and the proxy base URL

**Files:**
- Modify: `src/pitwall/seed.py` `_provider_from_seed` (line 367 branch) and `_provider_config` (after the gateway branch at line 513)
- Modify: `src/pitwall/api/provider_schemas.py` `_validate_url_config` (line 241)
- Test: `tests/test_seed_model_studio.py`, `tests/api/test_provider_schemas_model_studio.py`

**Interfaces:**
- Consumes: Task 11 `provider_settings`, `base_url`, `pricing_config`, `ModelStudioConfigError`.
- Produces: a seeded provider with `config.model_studio` (normalized), `config.openai_base_url` (derived), `config.supports_streaming = True`, and `config.cost` from the catalog unless the seed sets `cost`.

- [ ] **Step 1: Write the failing tests**

`tests/test_seed_model_studio.py` (same harness as `tests/test_gateway_seed.py`):

```python
"""Seeding a Model Studio provider derives its URL, cost, and settings from the catalog."""

from __future__ import annotations

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


def _payload(**provider_changes: object) -> dict:
    provider: dict[str, object] = {
        "name": "ms-qwen-flash",
        "capability": "coding.chat",
        "provider_type": "model_studio",
        "adapter": "model_studio",
        "credential_ref": "MODEL_STUDIO_API_KEY",
        "priority": 50,
        "model_studio": {
            "plan": "token-plan-personal",
            "tier": "pro",
            "model": "qwen3.8-flash",
            "renews_on": "2026-09-12",
        },
    }
    provider.update(provider_changes)
    return {
        "capabilities": [
            {
                "name": "coding.chat",
                "class": "llm",
                "cost_mode": "zero",
                "served_model_id": "qwen3.8-flash",
            }
        ],
        "providers": [provider],
    }


@pytest.mark.anyio
async def test_model_studio_seed_derives_url_cost_and_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_repos(monkeypatch)
    result = await apply_seed_data(_payload(), pool=MagicMock(), source=CapabilitySource.YAML)
    [provider] = result.providers
    assert provider.provider_type == ProviderType.MODEL_STUDIO
    assert provider.adapter_id == ProviderAdapterId.MODEL_STUDIO
    assert (
        provider.config["openai_base_url"]
        == "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
    )
    assert provider.config["cost"] == {"kind": "zero"}
    assert provider.config["model_studio"]["region"] == "ap-southeast-1"
    assert provider.config["supports_streaming"] is True
    assert "gpu_class" not in provider.config


@pytest.mark.anyio
async def test_model_studio_seed_refuses_bad_settings_by_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_repos(monkeypatch)
    bad = {"plan": "token-plan-personal", "tier": "pro", "model": "kimi-k2.7-code"}
    with pytest.raises(SeedValidationError, match="model_not_eligible"):
        await apply_seed_data(
            _payload(model_studio=bad), pool=MagicMock(), source=CapabilitySource.YAML
        )


@pytest.mark.anyio
async def test_model_studio_requires_the_model_studio_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_repos(monkeypatch)
    with pytest.raises(
        SeedValidationError, match="model_studio providers require adapter: model_studio"
    ):
        await apply_seed_data(
            _payload(adapter="together"), pool=MagicMock(), source=CapabilitySource.YAML
        )
```

`tests/api/test_provider_schemas_model_studio.py`:

```python
from pitwall.api.provider_schemas import _validate_url_config
from pitwall.core.enums import ProviderType
import pytest

GOOD = {
    "model_studio": {"plan": "token-plan-personal", "tier": "pro", "model": "qwen3.8-flash"},
    "openai_base_url": "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
}


def test_derived_url_is_accepted() -> None:
    _validate_url_config(ProviderType.MODEL_STUDIO, None, GOOD)


def test_redirected_url_is_refused() -> None:
    with pytest.raises(ValueError, match="openai_base_url"):
        _validate_url_config(
            ProviderType.MODEL_STUDIO, None, {**GOOD, "openai_base_url": "https://evil.example/v1"}
        )


def test_invalid_settings_are_refused() -> None:
    with pytest.raises(ValueError, match="region_not_allowed"):
        _validate_url_config(
            ProviderType.MODEL_STUDIO,
            None,
            {**GOOD, "model_studio": {**GOOD["model_studio"], "region": "us-east-1"}},
        )
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_seed_model_studio.py tests/api/test_provider_schemas_model_studio.py -q 2>&1 | tail -5`
Expected: FAIL — seed requires `gpu_class`; `_validate_url_config` raises `config.openai_base_url requires runpod_endpoint_id`.

- [ ] **Step 3: Implement**

`seed.py` `_provider_from_seed`: extend the gateway branch:

```python
    if provider_type == ProviderType.OPENAI_GATEWAY:
        gpu_class = None
        if adapter_id != ProviderAdapterId.GATEWAY:
            raise SeedValidationError("openai_gateway providers require adapter: openai_gateway")
    elif provider_type == ProviderType.MODEL_STUDIO:
        gpu_class = None
        if adapter_id != ProviderAdapterId.MODEL_STUDIO:
            raise SeedValidationError("model_studio providers require adapter: model_studio")
    else:
```

`seed.py` `_provider_config`: insert before `if endpoint_id is not None:` (after the gateway branch returns):

```python
if provider_type == ProviderType.MODEL_STUDIO:
    section = _dict_value(
        spec.get("model_studio", config.get("model_studio", {})), "provider.model_studio"
    )
    try:
        settings = model_studio_catalog.provider_settings({"model_studio": section})
        if "cost" not in spec and "cost" not in (spec.get("config") or {}):
            config["cost"] = model_studio_catalog.pricing_config(settings)
    except model_studio_catalog.ModelStudioConfigError as exc:
        raise SeedValidationError(str(exc)) from exc
    config["model_studio"] = settings
    config["openai_base_url"] = model_studio_catalog.base_url(settings)
    config["supports_streaming"] = True
    return config
```

with `from pitwall.providers.model_studio import catalog as model_studio_catalog` at the top of `seed.py`. The early `config["cost"]` default of `{"mode": "per_second"}` is overwritten here when the seed sets no cost.

`api/provider_schemas.py` `_validate_url_config`: add at the top:

```python
if provider_type == ProviderType.MODEL_STUDIO:
    from pitwall.providers.model_studio import catalog as model_studio_catalog

    try:
        settings = model_studio_catalog.provider_settings(config)
    except model_studio_catalog.ModelStudioConfigError as exc:
        raise ValueError(str(exc)) from exc
    expected = model_studio_catalog.base_url(settings)
    if _optional_non_empty_config_string(config, "openai_base_url") not in (None, expected):
        raise ValueError(
            f"config.openai_base_url must be {expected!r} for provider_type 'model_studio'"
        )
    return
```

`routing/openai.py` needs no change: `openai_base_url_for_provider` returns the configured `openai_base_url`, which is now always the derived value.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_seed_model_studio.py tests/api/test_provider_schemas_model_studio.py -q 2>&1 | tail -3
uv run pytest tests/api tests -q -k "seed or provider_schema" 2>&1 | tail -3
```

Expected: new tests pass; filtered suites `0 failed`.

- [ ] **Step 5: Commit**

```bash
git add src/pitwall/seed.py src/pitwall/api/provider_schemas.py tests/test_seed_model_studio.py tests/api/test_provider_schemas_model_studio.py
git commit -m "feat(model-studio): seed and API validation with catalog-derived URL and cost"
```

---

### Task 15: OpenAI proxy — automation gate and request rewrite for Model Studio

**Files:**
- Modify: `src/pitwall/routing/fallback.py` `_providers_with_openai_urls` (line 184), `execute_openai_with_fallback` (line 91), `_send_until_headers` (line 199)
- Modify: `src/pitwall/providers/model_studio/catalog.py` (add `rewrite_proxy_body`)
- Test: `tests/routing/test_fallback_model_studio.py`

**Interfaces:**
- Consumes: Task 11 `provider_settings`, `require_automation`, `check_model`; `OpenAIProxyRequest`, `OpenAIProxyExecutionError`.
- Produces: `catalog.rewrite_proxy_body(body: bytes, settings: Mapping[str, Any]) -> bytes`; the proxy skips gate-closed Model Studio providers and records `automation_not_accepted` in `attempted_errors`.

- [ ] **Step 1: Write the failing tests** (`tests/routing/test_fallback_model_studio.py`)

```python
"""The OpenAI proxy gates Token Plan automation and bounds reasoning for Model Studio."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from pitwall.core.enums import ProviderAdapterId, ProviderType
from pitwall.core.models import Provider
from pitwall.providers.model_studio.catalog import provider_settings, rewrite_proxy_body
from pitwall.routing.fallback import (
    DEFAULT_OPENAI_FALLBACK_BUDGET_S,
    OpenAIProxyExecutionError,
    OpenAIProxyRequest,
    execute_openai_with_fallback,
)

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
URL = "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"


def _provider() -> Provider:
    return Provider(
        id="prov_ms",
        capability_id="cap_ms",
        name="prov_ms",
        adapter_id=ProviderAdapterId.MODEL_STUDIO,
        credential_ref="MODEL_STUDIO_API_KEY",
        provider_type=ProviderType.MODEL_STUDIO,
        config={
            "model_studio": {
                "plan": "token-plan-personal",
                "tier": "pro",
                "model": "qwen3.8-flash",
            },
            "openai_base_url": URL,
        },
        priority=1,
        enabled=True,
        health_status="healthy",
        updated_at=NOW,
    )


def _request(client: httpx.AsyncClient) -> OpenAIProxyRequest:
    return OpenAIProxyRequest(
        method="POST",
        path="chat/completions",
        headers={"content-type": "application/json", "authorization": "Bearer caller-token"},
        body=json.dumps(
            {"model": "qwen", "stream": False, "max_tokens": 50, "messages": []}
        ).encode(),
        client=client,
        fallback_budget_s=DEFAULT_OPENAI_FALLBACK_BUDGET_S,
    )


def test_rewrite_moves_max_tokens_and_requests_usage() -> None:
    settings = provider_settings(
        {"model_studio": {"plan": "token-plan-personal", "tier": "pro", "model": "qwen3.8-flash"}}
    )
    body = json.loads(
        rewrite_proxy_body(
            json.dumps(
                {"model": "gw/anything", "stream": True, "max_tokens": 50, "messages": []}
            ).encode(),
            settings,
        )
    )
    assert body == {
        "model": "qwen3.8-flash",
        "stream": True,
        "max_completion_tokens": 50,
        "messages": [],
        "stream_options": {"include_usage": True},
    }


def test_rewrite_keeps_max_tokens_for_reasoning_inclusive_models_and_non_json() -> None:
    settings = provider_settings(
        {"model_studio": {"plan": "token-plan-personal", "tier": "pro", "model": "glm-5.3"}}
    )
    body = json.loads(rewrite_proxy_body(b'{"max_tokens": 50, "messages": []}', settings))
    assert body["max_tokens"] == 50 and "max_completion_tokens" not in body
    assert rewrite_proxy_body(b"not json", settings) == b"not json"


@respx.mock
@pytest.mark.anyio
async def test_gate_closed_provider_is_skipped_with_a_named_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MODEL_STUDIO_TOKEN_PLAN_AUTOMATION", raising=False)
    route = respx.post(f"{URL}/chat/completions").mock(return_value=httpx.Response(200, json={}))
    async with httpx.AsyncClient() as client:
        with pytest.raises(OpenAIProxyExecutionError) as caught:
            await execute_openai_with_fallback(_request(client), [_provider()])
    assert "automation_not_accepted" in caught.value.attempted_errors["prov_ms"]
    assert not route.called


@respx.mock
@pytest.mark.anyio
async def test_accepted_provider_receives_the_rewritten_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_STUDIO_TOKEN_PLAN_AUTOMATION", "accept")
    monkeypatch.setenv("MODEL_STUDIO_API_KEY", "sk-sp-test")
    route = respx.post(f"{URL}/chat/completions").mock(
        return_value=httpx.Response(200, json={"id": "ok"})
    )
    async with httpx.AsyncClient() as client:
        result = await execute_openai_with_fallback(_request(client), [_provider()])
    assert result.response.status_code == 200
    sent = route.calls.last.request
    assert sent.headers["authorization"] == "Bearer sk-sp-test"
    body = json.loads(sent.content)
    assert (body["model"], body["max_completion_tokens"]) == (
        "qwen3.8-flash",
        50,
    ) and "max_tokens" not in body
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/routing/test_fallback_model_studio.py -q 2>&1 | tail -5`
Expected: `ImportError: cannot import name 'rewrite_proxy_body'`.

- [ ] **Step 3: Implement**

Append to `catalog.py`:

```python
def rewrite_proxy_body(body: bytes, settings: Mapping[str, Any]) -> bytes:
    """Pin the model, bound reasoning by the admitted ceiling, and request streaming usage."""
    try:
        payload = json.loads(body)
    except ValueError:
        return body
    if not isinstance(payload, dict):
        return body
    record = load_catalog()["models"][str(settings["model"])]
    payload["model"] = settings["model"]
    if (
        not record.get("maxTokensIncludesReasoning")
        and "max_tokens" in payload
        and "max_completion_tokens" not in payload
    ):
        payload["max_completion_tokens"] = payload.pop("max_tokens")
    if payload.get("stream") is True:
        options = payload.get("stream_options")
        payload["stream_options"] = {
            **(options if isinstance(options, Mapping) else {}),
            "include_usage": True,
        }
    return json.dumps(payload, separators=(",", ":")).encode()
```

In `fallback.py` import `from pitwall.providers.model_studio import catalog as model_studio_catalog` and `os` (already imported). Change `_providers_with_openai_urls` to also return skip reasons:

```python
def _providers_with_openai_urls(
    providers: Sequence[Provider],
    *,
    max_attempts: int,
) -> tuple[tuple[Provider, ...], dict[str, str]]:
    eligible: list[Provider] = []
    skipped: dict[str, str] = {}
    for provider in providers:
        if len(eligible) >= max_attempts:
            break
        if openai_base_url_for_provider(provider) is None:
            continue
        if provider.provider_type == ProviderType.MODEL_STUDIO:
            try:
                settings = model_studio_catalog.provider_settings(provider.config)
                model_studio_catalog.require_automation(settings, os.environ)
            except model_studio_catalog.ModelStudioConfigError as exc:
                skipped[provider.id] = str(exc)
                continue
        eligible.append(provider)
    return tuple(eligible), skipped
```

In `execute_openai_with_fallback`, unpack `eligible_providers, skipped = _providers_with_openai_urls(...)` and initialize `attempted_errors: dict[str, str] = dict(skipped)`. In `_send_until_headers`, compute the content:

```python
content = request_ctx.body
if provider.provider_type == ProviderType.MODEL_STUDIO and content:
    content = model_studio_catalog.rewrite_proxy_body(
        content, model_studio_catalog.provider_settings(provider.config)
    )
```

and pass `content=content` to `build_request`. `_provider_headers` already injects `Bearer $credential_ref` for Model Studio (it is neither gateway nor pod lease). If any other caller of `_providers_with_openai_urls` exists (`grep -rn "_providers_with_openai_urls" src`), update it to unpack the tuple.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/routing/test_fallback_model_studio.py tests/routing -q 2>&1 | tail -3
uv run pytest tests/api -q -k openai 2>&1 | tail -3
```

Expected: `0 failed`.

- [ ] **Step 5: Commit**

```bash
git add src/pitwall/routing/fallback.py src/pitwall/providers/model_studio/catalog.py tests/routing/test_fallback_model_studio.py
git commit -m "feat(model-studio): proxy gate and reasoning-bounded request rewrite"
```

---

### Task 16: Quota windows — Token Plan Credits and pay-as-you-go month-to-date spend

**Files:**
- Create: `src/pitwall/providers/model_studio/openapi.py`
- Modify: `src/pitwall/db/quota_repository.py` (add `refresh_window`)
- Modify: `src/pitwall/reconciler/__init__.py` — `_MODEL_STUDIO_PROVIDERS_SQL`, `fetch_model_studio_providers`, `_model_studio_quota_tick`, and a call from `_quota_poll` (line 1756)
- Test: `tests/providers/test_model_studio_openapi.py`, `tests/reconciler/test_model_studio_quota_poll.py`

**Interfaces:**
- Consumes: Task 11 (`provider_settings`, `is_token_plan`, `credits_window`, `automation_accepted`, `load_catalog`); `QuotaRecord`, `QuotaRepository`.
- Produces:
  - `openapi.acs3_authorization(...)` (same signature and vector as Task 8)
  - `async get_subscription_stats(environ, *, now, transport=None, nonce=None) -> SubscriptionStats | None`
  - `async get_billing_month_to_date(environ, *, model: str, now, transport=None, nonce=None) -> Decimal | None`
  - `QuotaRepository.refresh_window(provider_id, pool_key, *, free_type, window_start, reset_at, budget_units, used_units, tos_verdict, evidence_patch) -> None` (inserts or updates; merges `evidence` with `||`, preserving `evidence.lockout`)

- [ ] **Step 1: Write the OpenAPI tests** (`tests/providers/test_model_studio_openapi.py`)

```python
"""Broker ACS3 signing matches Agent Routing's reference vector; stats and billing parse."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import httpx
import pytest

from pitwall.providers.model_studio import openapi

NOW = dt.datetime(2026, 9, 26, 12, 0, tzinfo=dt.UTC)
KEYS = {openapi.ACCESS_KEY_ID_ENV: "AKID", openapi.ACCESS_KEY_SECRET_ENV: "secret"}


def test_reference_vectors() -> None:
    headers = {
        "x-acs-action": "GetSubscriptionStats",
        "x-acs-version": "2026-02-10",
        "x-acs-date": "2026-09-26T12:00:00Z",
        "x-acs-signature-nonce": "3f1d6c2e-0000-4000-8000-000000000001",
    }
    assert openapi.acs3_authorization(
        "GET",
        "modelstudio.ap-southeast-1.aliyuncs.com",
        "/tokenplan/subscription/stats",
        {},
        headers,
        b"",
        access_key_id="TESTAKID",
        access_key_secret="test-secret",
    ).endswith("Signature=3e90126c0a15bd300a99078246f80342877fa314c10ff4275277d6d5ba98a0ce")
    billing = {
        **headers,
        "x-acs-action": "GetBillingOverview",
        "x-acs-signature-nonce": "3f1d6c2e-0000-4000-8000-000000000002",
    }
    assert openapi.acs3_authorization(
        "GET",
        "modelstudio.ap-southeast-1.aliyuncs.com",
        "/modelstudio/billing/overview",
        {"billMonth": "2026-09", "groupBy": '[{"code":"BASE_MODEL"}]'},
        billing,
        b"",
        access_key_id="TESTAKID",
        access_key_secret="test-secret",
    ).endswith("Signature=5edb3f16d4b3d0448e40c23cc0dda653782f6640f581500129e4eb037bfd4245")


@pytest.mark.anyio
async def test_no_access_key_means_no_request() -> None:
    assert await openapi.get_subscription_stats({}, now=NOW) is None
    assert await openapi.get_billing_month_to_date({}, model="qwen3.8-flash", now=NOW) is None


@pytest.mark.anyio
async def test_stats_parse() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/tokenplan/subscription/stats"
        assert request.headers["authorization"].startswith("ACS3-HMAC-SHA256 Credential=AKID,")
        return httpx.Response(
            200,
            json={
                "Data": {
                    "Items": [
                        {
                            "SeatRefreshTime": 1759622400000,
                            "SeatCredits": 180000,
                            "SeatRemainingCredits": 1000,
                        }
                    ]
                }
            },
        )

    stats = await openapi.get_subscription_stats(
        KEYS, now=NOW, transport=httpx.MockTransport(handler)
    )
    assert stats is not None
    assert (stats.total_credits, stats.remaining_credits) == (Decimal(180000), Decimal(1000))
    assert stats.reset_at == dt.datetime.fromtimestamp(1759622400, tz=dt.UTC)


@pytest.mark.anyio
async def test_billing_month_to_date_for_the_model() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/modelstudio/billing/overview"
        assert request.url.params["billMonth"] == "2026-09"
        assert json.loads(request.url.params["groupBy"]) == [{"code": "BASE_MODEL"}]
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "currency": "USD",
                    "groups": [
                        {"key": "qwen3.8-flash", "amount": "12.34"},
                        {"key": "qwen3.8-max", "amount": "99"},
                    ],
                },
            },
        )

    spend = await openapi.get_billing_month_to_date(
        KEYS, model="qwen3.8-flash", now=NOW, transport=httpx.MockTransport(handler)
    )
    assert spend == Decimal("12.34")
```

- [ ] **Step 2: Write the quota-poll tests** (`tests/reconciler/test_model_studio_quota_poll.py`)

Reuse `_make_mock_pool` from `tests/reconciler/test_quota_poll.py` (copy it into this module).

```python
NOW = dt.datetime(2026, 9, 26, 12, 0, tzinfo=dt.UTC)


def _row(**settings: Any) -> dict[str, Any]:
    base = {
        "plan": "token-plan-personal",
        "tier": "pro",
        "model": "qwen3.8-flash",
        "renews_on": "2026-09-12",
    }
    base.update(settings)
    return {"id": "ms-flash", "config": {"model_studio": base}}


async def _poll(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, Any]],
    *,
    stats: Any = None,
    spend: Any = None,
    env: dict[str, str] | None = None,
) -> AsyncMock:
    repo = AsyncMock()
    repo.list_all.return_value = ()
    monkeypatch.setattr("pitwall.reconciler.QuotaRepository", lambda _pool: repo)
    monkeypatch.setattr("pitwall.reconciler.fetch_gateway_providers", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        "pitwall.reconciler.fetch_model_studio_providers", AsyncMock(return_value=rows)
    )
    monkeypatch.setattr(
        "pitwall.reconciler.model_studio_openapi.get_subscription_stats",
        AsyncMock(return_value=stats),
    )
    monkeypatch.setattr(
        "pitwall.reconciler.model_studio_openapi.get_billing_month_to_date",
        AsyncMock(return_value=spend),
    )
    for key in ("MODEL_STUDIO_TOKEN_PLAN_AUTOMATION",):
        monkeypatch.delenv(key, raising=False)
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    await _quota_poll({"db_pool": _make_mock_pool(), "now": NOW})
    return repo


async def test_token_plan_window_from_renewal_without_stats_and_gate_closed(monkeypatch) -> None:
    repo = await _poll(monkeypatch, [_row()])
    kwargs = repo.refresh_window.await_args.kwargs
    assert kwargs["free_type"] == "subscription-credits"
    assert (kwargs["window_start"], kwargs["reset_at"]) == (
        dt.datetime(2026, 9, 12, tzinfo=dt.UTC),
        dt.datetime(2026, 10, 12, tzinfo=dt.UTC),
    )
    assert kwargs["budget_units"] == Decimal(180000)
    assert kwargs["used_units"] is None
    assert kwargs["tos_verdict"] == "avoid"
    assert kwargs["evidence_patch"]["source"] == "configured-tier"


async def test_token_plan_stats_and_acceptance(monkeypatch) -> None:
    stats = SubscriptionStats(
        window_start=NOW - dt.timedelta(days=4),
        reset_at=NOW + dt.timedelta(days=26),
        total_credits=Decimal(180000),
        remaining_credits=Decimal(170000),
    )
    repo = await _poll(
        monkeypatch, [_row()], stats=stats, env={"MODEL_STUDIO_TOKEN_PLAN_AUTOMATION": "accept"}
    )
    kwargs = repo.refresh_window.await_args.kwargs
    assert (kwargs["used_units"], kwargs["tos_verdict"]) == (Decimal(10000), "caution")
    assert kwargs["evidence_patch"]["source"] == "openapi-stats"


async def test_pay_as_you_go_records_month_to_date_spend(monkeypatch) -> None:
    repo = await _poll(
        monkeypatch,
        [_row(plan="pay-as-you-go", tier=None, region="ap-southeast-1", renews_on=None)],
        spend=Decimal("12.34"),
    )
    kwargs = repo.refresh_window.await_args.kwargs
    assert kwargs["free_type"] == "pay-as-you-go"
    assert kwargs["budget_units"] is None and kwargs["used_units"] == Decimal("12.34")
    assert kwargs["tos_verdict"] == "ok"
    assert (kwargs["window_start"], kwargs["reset_at"]) == (
        dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
        dt.datetime(2026, 10, 1, tzinfo=dt.UTC),
    )


async def test_one_bad_provider_does_not_stop_the_tick(monkeypatch) -> None:
    repo = await _poll(monkeypatch, [{"id": "broken", "config": {}}, _row()])
    assert repo.refresh_window.await_count == 1
```

`_row(tier=None, renews_on=None)` must drop keys whose value is `None` before building the config (filter the dict).

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/providers/test_model_studio_openapi.py tests/reconciler/test_model_studio_quota_poll.py -q 2>&1 | tail -5`
Expected: `ModuleNotFoundError: … model_studio.openapi`.

- [ ] **Step 4: Implement `openapi.py`**

```python
"""Model Studio OpenAPI reads (ACS3-HMAC-SHA256); used only for quota and billing evidence."""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import quote

import httpx

from pitwall.providers.model_studio.catalog import load_catalog

ACCESS_KEY_ID_ENV = "ALIBABA_CLOUD_ACCESS_KEY_ID"
ACCESS_KEY_SECRET_ENV = "ALIBABA_CLOUD_ACCESS_KEY_SECRET"
_ALGORITHM = "ACS3-HMAC-SHA256"
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _encode(value: str) -> str:
    return quote(value, safe="-_.~")


def acs3_authorization(
    method: str,
    host: str,
    path: str,
    query: Mapping[str, str],
    headers: Mapping[str, str],
    body: bytes,
    *,
    access_key_id: str,
    access_key_secret: str,
) -> str:
    payload_hash = hashlib.sha256(body).hexdigest()
    canonical_headers = {key.lower(): value.strip() for key, value in headers.items()}
    canonical_headers["host"] = host
    canonical_headers["x-acs-content-sha256"] = payload_hash
    names = sorted(
        name
        for name in canonical_headers
        if name in {"host", "content-type"} or name.startswith("x-acs-")
    )
    header_block = "".join(f"{name}:{canonical_headers[name]}\n" for name in names)
    signed = ";".join(names)
    query_block = "&".join(f"{_encode(k)}={_encode(v)}" for k, v in sorted(query.items()))
    canonical = "\n".join(
        [method, quote(path or "/", safe="/-_.~"), query_block, header_block, signed, payload_hash]
    )
    string_to_sign = f"{_ALGORITHM}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
    signature = hmac.new(
        access_key_secret.encode(), string_to_sign.encode(), hashlib.sha256
    ).hexdigest()
    return f"{_ALGORITHM} Credential={access_key_id},SignedHeaders={signed},Signature={signature}"


@dataclass(frozen=True, slots=True)
class SubscriptionStats:
    window_start: dt.datetime
    reset_at: dt.datetime
    total_credits: Decimal
    remaining_credits: Decimal


async def _signed_get(
    environ: Mapping[str, str],
    *,
    action: str,
    path: str,
    query: Mapping[str, str],
    now: dt.datetime,
    transport: httpx.AsyncBaseTransport | None,
    nonce: str | None,
) -> Mapping[str, object] | None:
    key_id = environ.get(ACCESS_KEY_ID_ENV, "")
    secret = environ.get(ACCESS_KEY_SECRET_ENV, "")
    if not key_id or not secret:
        return None
    spec = load_catalog()["openApi"]
    host = str(spec["host"])
    headers = {
        "x-acs-action": action,
        "x-acs-version": str(spec["version"]),
        "x-acs-date": now.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "x-acs-signature-nonce": nonce or str(uuid.uuid4()),
    }
    authorization = acs3_authorization(
        "GET", host, path, query, headers, b"", access_key_id=key_id, access_key_secret=secret
    )
    async with httpx.AsyncClient(
        base_url=f"https://{host}", timeout=15.0, transport=transport
    ) as client:
        response = await client.get(
            path,
            params=dict(query),
            headers={
                **headers,
                "x-acs-content-sha256": _EMPTY_SHA256,
                "Authorization": authorization,
                "Accept": "application/json",
            },
        )
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, Mapping) else None


async def get_subscription_stats(
    environ: Mapping[str, str],
    *,
    now: dt.datetime,
    transport: httpx.AsyncBaseTransport | None = None,
    nonce: str | None = None,
) -> SubscriptionStats | None:
    payload = await _signed_get(
        environ,
        action="GetSubscriptionStats",
        path="/tokenplan/subscription/stats",
        query={},
        now=now,
        transport=transport,
        nonce=nonce,
    )
    if payload is None:
        return None
    data = payload.get("Data")
    items = data.get("Items") if isinstance(data, Mapping) else None
    if not isinstance(items, list) or not items:
        return None
    total = sum((Decimal(str(item.get("SeatCredits") or 0)) for item in items), Decimal(0))
    remaining = sum(
        (Decimal(str(item.get("SeatRemainingCredits") or 0)) for item in items), Decimal(0)
    )
    reset_at = dt.datetime.fromtimestamp(
        min(int(item["SeatRefreshTime"]) for item in items) / 1000, tz=dt.UTC
    )
    return SubscriptionStats(reset_at - dt.timedelta(days=30), reset_at, total, remaining)


async def get_billing_month_to_date(
    environ: Mapping[str, str],
    *,
    model: str,
    now: dt.datetime,
    transport: httpx.AsyncBaseTransport | None = None,
    nonce: str | None = None,
) -> Decimal | None:
    query = {
        "billMonth": now.strftime("%Y-%m"),
        "groupBy": json.dumps([{"code": "BASE_MODEL"}], separators=(",", ":")),
    }
    payload = await _signed_get(
        environ,
        action="GetBillingOverview",
        path="/modelstudio/billing/overview",
        query=query,
        now=now,
        transport=transport,
        nonce=nonce,
    )
    if payload is None:
        return None
    data = payload.get("data")
    groups = data.get("groups") if isinstance(data, Mapping) else None
    for group in groups if isinstance(groups, list) else []:
        if isinstance(group, Mapping) and group.get("key") == model:
            return Decimal(str(group.get("amount") or "0"))
    return Decimal(0)
```

- [ ] **Step 5: Implement the repository method and the poll**

`db/quota_repository.py`:

```python
    async def refresh_window(
        self,
        provider_id: str,
        pool_key: str,
        *,
        free_type: str,
        window_start: dt.datetime | None,
        reset_at: dt.datetime | None,
        budget_units: Decimal | None,
        used_units: Decimal | None,
        tos_verdict: str,
        evidence_patch: Mapping[str, Any],
    ) -> None:
        """Upsert a provider-observed window; ``None`` used_units keeps the stored count."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO pitwall.provider_quotas
                    (provider_id, pool_key, free_type, window_start, reset_at, budget_units, used_units, tos_verdict, evidence, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, COALESCE($7, '0'), $8, $9::jsonb, now())
                ON CONFLICT (provider_id, pool_key) DO UPDATE SET
                    free_type = EXCLUDED.free_type,
                    used_units = CASE
                        WHEN $7 IS NOT NULL THEN $7
                        WHEN pitwall.provider_quotas.window_start IS DISTINCT FROM EXCLUDED.window_start THEN '0'
                        ELSE pitwall.provider_quotas.used_units END,
                    window_start = EXCLUDED.window_start,
                    reset_at = EXCLUDED.reset_at,
                    budget_units = EXCLUDED.budget_units,
                    tos_verdict = EXCLUDED.tos_verdict,
                    evidence = pitwall.provider_quotas.evidence || EXCLUDED.evidence,
                    updated_at = now()
                """,
                provider_id,
                pool_key,
                free_type,
                window_start,
                reset_at,
                str(budget_units) if budget_units is not None else None,
                str(used_units) if used_units is not None else None,
                tos_verdict,
                json.dumps(dict(evidence_patch)),
            )
```

(add `json`, `Mapping`, `Any`, `Decimal`, `dt` imports as needed; if the pool registers a JSONB codec, pass the mapping instead of `json.dumps` — match `upsert`'s existing evidence argument.)

`reconciler/__init__.py`: add imports `from pitwall.providers.model_studio import catalog as model_studio_catalog` and `from pitwall.providers.model_studio import openapi as model_studio_openapi`, then:

```python
_MODEL_STUDIO_PROVIDERS_SQL = """
    SELECT id, config FROM pitwall.providers
    WHERE enabled = true AND provider_type = 'model_studio'
    ORDER BY id
"""


async def fetch_model_studio_providers(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(_MODEL_STUDIO_PROVIDERS_SQL)
    return [dict(row) for row in rows]


async def _model_studio_quota_tick(
    repo: QuotaRepository, prov: Mapping[str, Any], now: dt.datetime
) -> None:
    settings = model_studio_catalog.provider_settings(prov.get("config") or {})
    plan = str(settings["plan"])
    if not model_studio_catalog.is_token_plan(plan):
        spend = await model_studio_openapi.get_billing_month_to_date(
            os.environ, model=str(settings["model"]), now=now
        )
        start, reset = _quota_window("recurring-monthly", now)
        await repo.refresh_window(
            prov["id"],
            "",
            free_type="pay-as-you-go",
            window_start=start,
            reset_at=reset,
            budget_units=None,
            used_units=spend,
            tos_verdict="ok",
            evidence_patch={
                "source": "GetBillingOverview" if spend is not None else "not-configured",
                "currency": "USD",
                "model": settings["model"],
            },
        )
        return
    tier = model_studio_catalog.load_catalog()["plans"][plan]["tiers"][settings["tier"]]
    accepted = model_studio_catalog.automation_accepted(settings, os.environ)
    stats = await model_studio_openapi.get_subscription_stats(os.environ, now=now)
    if stats is not None:
        start, reset = stats.window_start, stats.reset_at
        budget: Decimal | None = stats.total_credits
        used: Decimal | None = stats.total_credits - stats.remaining_credits
        source = "openapi-stats"
    else:
        start, reset = (
            model_studio_catalog.credits_window(settings["renews_on"], now)
            if settings.get("renews_on")
            else (None, None)
        )
        budget, used, source = Decimal(tier["credits"]), None, "configured-tier"
    await repo.refresh_window(
        prov["id"],
        "",
        free_type="subscription-credits",
        window_start=start,
        reset_at=reset,
        budget_units=budget,
        used_units=used,
        tos_verdict="caution" if accepted else "avoid",
        evidence_patch={
            "source": source,
            "plan": plan,
            "tier": settings["tier"],
            "automation": "accepted" if accepted else "not-accepted",
        },
    )
```

At the end of `_quota_poll`, after the gateway loop:

```python
    for prov in await fetch_model_studio_providers(pool):
        try:
            await _model_studio_quota_tick(repo, prov, now)
        except Exception as exc:  # reason: one provider's quota write must not stop the tick
            log.warning("model studio quota poll failed for %s: %s", prov.get("id"), redact_text(exc))
```

`Decimal`, `os`, `Mapping` must be imported in `reconciler/__init__.py` (check the existing imports).

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/providers/test_model_studio_openapi.py tests/reconciler -q 2>&1 | tail -3
make test-int 2>&1 | tail -5
```

Expected: new tests pass; `tests/reconciler` `0 failed`; `make test-int` `0 failed` (add one integration assertion in `tests/db/test_model_studio_migration.py::test_model_studio_provider_and_credits_quota_insert` calling `QuotaRepository(pool).refresh_window(...)` twice and checking that a pre-set `evidence.lockout` survives the second call).

- [ ] **Step 7: Commit**

```bash
git add src/pitwall/providers/model_studio/openapi.py src/pitwall/db/quota_repository.py src/pitwall/reconciler/__init__.py tests/providers/test_model_studio_openapi.py tests/reconciler/test_model_studio_quota_poll.py tests/db/test_model_studio_migration.py
git commit -m "feat(model-studio): Credits and billing quota windows from signed OpenAPI reads"
```

---

### Task 17: Documentation, environment example, and API baseline

**Files:**
- Create: `docs/operator/model-studio.md`
- Modify: `packages/agent-routing/docs/routes.md` (endpoint kind, `add-model-studio-endpoint`, automatic limits, probe statuses)
- Modify: `docs/sdlc/20-provider-plugins.md` (Model Studio row in the adapter table; the `model_studio` provider type)
- Modify: the support matrix (`grep -rln "lambda_cloud" docs | grep -i matrix` to locate it) — add the Model Studio row
- Modify: `CHANGELOG.md` (root, Unreleased) and `packages/agent-routing/CHANGELOG.md` (Unreleased)
- Modify: `.env.example` (Model Studio section)
- Modify: `docs/api/openapi-baseline.json` (regenerated)

- [ ] **Step 1: Write the operator guide** `docs/operator/model-studio.md` with these sections, each concrete (commands, fields, example YAML):
  1. **Plans** — Token Plan Personal/Team vs pay-as-you-go; Singapore-only Token Plan; `sk-sp-` keys; the 30-day cycle; exhaustion blocks with no overflow; prices differ by region scope and only Singapore is catalogued.
  2. **The Token Plan terms** — quote the interactive-only clause; the penalty (suspension or key banning); what `MODEL_STUDIO_TOKEN_PLAN_AUTOMATION=accept` / `--accept-token-plan-automation` changes; interactive sessions are never gated.
  3. **Agent Routing setup** — `routes add-model-studio-endpoint ms --plan token-plan-personal --tier pro --renews-on 2026-09-12 --accept-token-plan-automation`; `routes add flash --model qwen3.8-flash --endpoint ms --harness opencode` (without `--harness` the route is pinned to `defaults.endpointHarness`, `qwen` out of the box); `routes sync --harness opencode`; `routes probe flash`; concurrency and `endpoint_busy` (exit 75); the local exhaustion lockout and where it lives (`$XDG_STATE_HOME/subagent-model-routing/endpoint-slots/<endpoint>/exhausted.json`) and how to clear it (delete the file after renewal).
  4. **Broker setup** — seed YAML:

     ```yaml
     providers:
       - name: ms-qwen38-flash
         capability: "coding.chat"
         provider_type: "model_studio"
         adapter: "model_studio"
         credential_ref: "MODEL_STUDIO_API_KEY"
         priority: 50
         model_studio:
           plan: "token-plan-personal"
           tier: "pro"
           model: "qwen3.8-flash"
           renews_on: "2026-09-12"
     ```

     Pay-as-you-go variant with `region` and `workspace`; cost derived from the catalog; the quota rows (`subscription-credits`, `pay-as-you-go`) and `tos_verdict` meaning; the AccessKey variables and what they unlock.
  5. **Errors** — the section 8 table of the spec, with the Pitwall classification and what the operator does.
- [ ] **Step 2: Update `packages/agent-routing/docs/routes.md`** with the endpoint kind fields table (from Task 4's schema), the new command and flags, the automatic limits and the harness pin (`--harness`, else `defaults.endpointHarness`), the anthropic-protocol rule, and the probe statuses `misconfigured` / `quota-exhausted`.
- [ ] **Step 3: Update `docs/sdlc/20-provider-plugins.md`** — add Model Studio to the adapter inventory with its capabilities (`sync_inference`, `availability`), credential (`MODEL_STUDIO_API_KEY`), provider type, and the catalog parity rule.
- [ ] **Step 4: `.env.example`** — add after the gateway section:

```text
# --- Alibaba Cloud Model Studio (docs/operator/model-studio.md) ---
# MODEL_STUDIO_API_KEY=                     # Token Plan (sk-sp-) or pay-as-you-go key; referenced by credential_ref
# MODEL_STUDIO_TOKEN_PLAN_AUTOMATION=       # set to "accept" to allow broker use of a Token Plan key (terms allow interactive use only)
# ALIBABA_CLOUD_ACCESS_KEY_ID=              # optional: Credits stats and billing reads (ACS3-signed, read-only)
# ALIBABA_CLOUD_ACCESS_KEY_SECRET=          # optional: pairs with the AccessKey id; never commit a real value
```

- [ ] **Step 5: Changelogs** — root Unreleased: "Alibaba Cloud Model Studio provider (`model_studio`), per-token cached-input and context-tier pricing, explicit reset times honoured by the lockout table, generic 429 classifier no longer treats a bare 'quota' as monthly, migration 0035." Agent Routing Unreleased: "`model-studio` endpoint kind, `routes add-model-studio-endpoint`, catalog limits and effort, OpenCode/Pi sync, Token Plan automation gate, endpoint concurrency slots, Model Studio readiness."
- [ ] **Step 6: Regenerate the API baseline and run the doc gates**

```bash
uv run python tools/ci/export_openapi.py --output docs/api/openapi-baseline.json
make openapi-check 2>&1 | tail -3
make docs-check 2>&1 | tail -5
git ls-files -z | xargs -0 -r -n 200 uv run python tools/guards/repo_text_policy.py 2>&1 | tail -3
uv run pytest tests/test_env_coverage.py tests/release_acceptance/test_config_inventory.py -q 2>&1 | tail -3
```

Expected: the baseline diff adds `model_studio` to the provider-type and adapter enums only; `openapi-check` passes; `docs-check` passes; the text-policy guard reports no findings; env and config-inventory tests pass. (The guard loads the gitignored overlay `tools/guards/repo_text_policy.local` automatically in this checkout.)

- [ ] **Step 7: Commit**

```bash
git add docs/operator/model-studio.md packages/agent-routing/docs/routes.md docs/sdlc/20-provider-plugins.md CHANGELOG.md packages/agent-routing/CHANGELOG.md .env.example docs/api/openapi-baseline.json
git add $(grep -rln "model_studio\|Model Studio" docs --include='*matrix*' 2>/dev/null)
git commit -m "docs(model-studio): operator guide, routes reference, provider SDLC, changelogs"
```

---

### Task 18: Release acceptance and full gates

**Files:**
- Modify (only if the acceptance tools report unbound surfaces): `tools/release_acceptance/bind_surfaces.py` inputs and the discovery review files it names
- No new product code.

- [ ] **Step 1: Bind surfaces and run release acceptance**

```bash
uv run python -m tools.release_acceptance.bind_surfaces 2>&1 | tail -10
uv run pytest tests/release_acceptance -q 2>&1 | tail -5
```

Expected: `bind_surfaces` reports the new CLI command, env vars, migration, and provider type as bound (if it lists them as unbound, add their bindings in the files it names and rerun until it reports none); release-acceptance tests `0 failed`.

- [ ] **Step 2: Secrets scan** (the tool scans a temporary copy of the baseline itself)

```bash
uv run python tools/security/check_secrets.py 2>&1 | tail -5
```

Expected: no new findings. The ACS3 test vector uses the literal `test-secret`; if detect-secrets flags it, add an inline `# pragma: allowlist secret` on that line rather than changing the baseline.

- [ ] **Step 3: Full suites**

```bash
make test-fast 2>&1 | tail -5
make test-int 2>&1 | tail -5
cd packages/agent-routing && export PATH="$PWD/.venv/bin:$PATH" && ruff check runtime tests tools scripts/pitwall-agent-routing && mypy --python-version 3.14 runtime/model_routing tools scripts/pitwall-agent-routing && python3 tools/validate_json_schemas.py && python3 tools/validate_registry.py && python3 tools/check_generated.py && python3 -m unittest discover -s tests 2>&1 | tail -3; cd ../..
uv run ruff check src tests && uv run mypy src 2>&1 | tail -2
```

Expected: every command exits 0; unit and integration suites `0 failed`; Agent Routing discover `OK`.

- [ ] **Step 4: Commit any binding changes**

```bash
git add tools/release_acceptance docs/release 2>/dev/null
git diff --cached --quiet || git commit -m "chore(release): bind Model Studio surfaces for release acceptance"
```

---

### Task 19: Opt-in live checks on qwen3.8-flash and the evidence record

**Files:**
- Create: `tests/live/test_model_studio_live.py`
- Create: `docs/evidence/2026-09-26-model-studio-live.md`

Requires the maintainer's Token Plan key exported as `MODEL_STUDIO_API_KEY` (and optionally the AccessKey pair) on the machine that runs it. If the key is not present in the environment, stop and ask the maintainer to export it; do not read credential files.

- [ ] **Step 1: Write the live test module** (skipped unless both gates are set)

```python
"""Live Model Studio checks; run with PITWALL_MODEL_STUDIO_LIVE=1 and --run-live."""

from __future__ import annotations

import datetime as dt
import os

import pytest

from pitwall.core.enums import ProviderAdapterId, ProviderType
from pitwall.core.models import Provider as ProviderRecord
from pitwall.providers.interface import CredentialReference
from pitwall.providers.model_studio import ModelStudioProvider, openapi

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("PITWALL_MODEL_STUDIO_LIVE") != "1", reason="live gate off"),
]


def _record() -> ProviderRecord:
    return ProviderRecord(
        id="live_ms",
        capability_id="cap_live",
        name="live",
        adapter_id=ProviderAdapterId.MODEL_STUDIO,
        credential_ref="MODEL_STUDIO_API_KEY",
        provider_type=ProviderType.MODEL_STUDIO,
        config={
            "model_studio": {
                "plan": "token-plan-personal",
                "tier": "pro",
                "model": "qwen3.8-flash",
                "automation": "accept",
            },
            "cost": {"kind": "zero"},
        },
    )


@pytest.mark.anyio
async def test_live_inference_streams_usage() -> None:
    result = await ModelStudioProvider().infer(
        credentials=CredentialReference(name="MODEL_STUDIO_API_KEY"),
        provider_record=_record(),
        payload={
            "messages": [{"role": "user", "content": "Reply with the single word: pong"}],
            "max_tokens": 64,
            "enable_thinking": False,
        },
    )
    assert result.content and "pong" in result.content.lower()
    assert result.prompt_tokens and result.completion_tokens


@pytest.mark.anyio
async def test_live_subscription_stats_or_documented_absence() -> None:
    stats = await openapi.get_subscription_stats(os.environ, now=dt.datetime.now(dt.UTC))
    if stats is None:
        pytest.skip("AccessKey not configured or Personal Edition exposes no stats")
    assert stats.total_credits > 0
```

Use the repo's existing live marker and gate helper if the conftest defines one (`grep -n "run_live\|RUN_LIVE_OPTION" tests/conftest.py`), matching how other live tests declare it.

- [ ] **Step 2: Run the live checks** (the maintainer's key in the environment; one prompt each, qwen3.8-flash)

```bash
PITWALL_MODEL_STUDIO_LIVE=1 uv run pytest tests/live/test_model_studio_live.py --run-live -q 2>&1 | tail -5
cd packages/agent-routing
.venv/bin/python scripts/pitwall-agent-routing routes add-model-studio-endpoint ms-live --plan token-plan-personal --tier pro --accept-token-plan-automation
.venv/bin/python scripts/pitwall-agent-routing routes add ms-flash --model qwen3.8-flash --endpoint ms-live --harness opencode
.venv/bin/python scripts/pitwall-agent-routing routes sync --harness opencode --yes
.venv/bin/python scripts/pitwall-agent-routing routes probe ms-flash
printf 'Reply with the single word: pong\n' > /tmp/ms-pong.md
bash scripts/route-shim.sh ms-flash /tmp/ms-pong.md 2>&1 | tail -5
bash scripts/route-shim.sh ms-flash@qwen /tmp/ms-pong.md 2>&1 | tail -5
cd ../..
```

Expected: live pytest `1 passed` plus the stats test passed or skipped with its reason; probe `reachable`; both dispatches end `SHIM-DONE exit=0` with `pong` in the output. Then one proxied request through a running broker seeded with the Task 17 YAML (`curl -s $PITWALL_API_URL/v1/chat/completions -H "Authorization: Bearer $PITWALL_API_TOKEN" -d '{"model":"<capability model id>","messages":[{"role":"user","content":"pong?"}],"max_tokens":32}'`) returns HTTP 200. If `GetBillingOverview` or `GetSubscriptionStats` rejects the query encoding, fix `openapi.py`/`model_studio_openapi.py` (both halves), rerun, and record the correction.

- [ ] **Step 3: Record the evidence** in `docs/evidence/2026-09-26-model-studio-live.md`: date, commit SHA, each command above with its exit code and the relevant output lines (token counts, probe detail, SHIM-DONE lines, HTTP status), whether Personal Edition exposes stats, and any correction made in Step 2. No keys, account ids, hostnames, or personal names.

- [ ] **Step 4: Remove the live endpoint and route from the maintainer's routes.json if they were created only for the check** (list them first; ask before removing anything that predates this task), run the text-policy guard, and commit:

```bash
git ls-files -z | xargs -0 -r -n 200 uv run python tools/guards/repo_text_policy.py 2>&1 | tail -3
git add tests/live/test_model_studio_live.py docs/evidence/2026-09-26-model-studio-live.md
git commit -m "docs(evidence): Model Studio live checks on qwen3.8-flash"
```

---

## Spec amendments made with this plan

Reading the code while planning forced these corrections, now written into the spec:

1. The catalog lives at `resources/config/model-studio.json` (the resource loader accepts only `config/` and `schemas/`).
2. Prices differ by region scope; the catalog records Singapore ("International") prices per model, including implicit-cache and above-256,000-token tiers, and deepseek-v4.1-flash's busy-hour price. Other regions need an operator-supplied cost.
3. `MODEL_STUDIO_RENEWS_ON` is a date, because the Token Plan cycle is 30 days from purchase, not a calendar day of month.
4. The endpoint-creation command is `routes add-model-studio-endpoint` with `--model-studio-workspace` (`--workspace` already exists), and `routes add --model M --endpoint E` fills limits automatically (no `--model-studio` flag). The stored `baseUrl` is the derived value and is checked on load.
5. Concurrency uses per-endpoint `flock` slots, which the kernel releases when a dispatch crashes, instead of counting run-store records.
6. Readiness uses `GET /api/v1/models?model=`; the Agent Routing exhaustion lockout comes from the dispatch's own output.
7. Effort on the Anthropic protocol is refused; OpenCode receives `variants`.
8. Admission ceilings: pricing carries `default_max_output_tokens`; the adapter and proxy send the ceiling as `max_completion_tokens` (or `max_tokens` where it already includes reasoning).
9. Billing reads fill the quota table for pay-as-you-go, not `ACTUAL_COST`, because the billing API cannot map charges to workloads.
10. The Token Plan automation gate also sets `tos_verdict` (`avoid`/`caution`), and the proxy skips gate-closed providers by name.
11. The lockout table honours explicit reset times for every non-permanent reason (the 60-second cooldown); billing states lock for one hour.

## Continuation: corrections from the Lane A review

C1. Task 5 pinned `defaults.harness` (the vendor-model default) instead of `defaults.endpointHarness`,
because the plan's test expected `opencode`, assuming that was the endpoint default; it is `qwen`.
Fixed on `feat/model-studio-a` in `0300017`: the pin uses `defaults.endpointHarness`, the test sets
the two defaults apart, and a second test keeps an explicit `--harness`. Tasks 17 and 19 now pass
`--harness opencode` where they mean OpenCode.

C2. Task 12's `test_cached_tokens_use_the_cached_price` sent a 1,000,000-token prompt but expected
base-tier prices; above 256,000 input tokens the tier applies. The lane kept the expected value and
made `PerTokenPricing.estimate` skip tiers whenever cached tokens were reported, under-pricing long
cached prompts. Fixed on `feat/model-studio-b` in `e706496`: tiers apply to every request, and the
tests cover cached tokens below (0.048) and above (0.72) the tier.

C3. The live check (`f701c2f`) found that the Token Plan host answers the native `GET /api/v1/models`
with 404, so `routes probe` reported `down`. Both the Agent Routing probe (`route_probe._probe_model_studio`)
and the broker adapter's `availability` now read the OpenAI-compatible `…/compatible-mode/v1/models` list,
which the live check showed lists `qwen3.8-flash`. The probe tests route only that path and fail if the
native base URL is used; the adapter gained its first availability test.
