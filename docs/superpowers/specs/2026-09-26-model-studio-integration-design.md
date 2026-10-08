# Alibaba Cloud Model Studio integration — design

Status: approved for planning on 2026-09-26 (the maintainer waived section-by-section review and asked for the
spec and implementation plan together).
Research: `$PITWALL_EVIDENCE_ROOT/research/alibaba-model-studio-20260926/report.md` (994 Model
Studio pages fetched and keyword-scanned, about 60 read in full; every fact below that concerns
Alibaba cites a documentation page slug under `alibabacloud.com/help/en/model-studio/`).

## 1. Goal and decisions

Make Alibaba Cloud Model Studio a first-class provider in both halves of Pitwall: Agent Routing
(coding-agent dispatch) and the broker (REST/MCP inference with budgets). The first user runs a
Token Plan Personal Pro subscription on `ed8`, but nothing may depend on that machine, that plan,
or that account: any user with any Model Studio plan or a pay-as-you-go key configures it the same
way.

Decisions made on 2026-09-26:

| Question | Decision |
| --- | --- |
| Edition | Personal, Pro tier (180,000 Credits per 30-day cycle, 6–8 concurrent agents) |
| Token Plan terms (interactive use only) | The risk of automated use is accepted; Pitwall records that acceptance per installation instead of assuming it |
| Machines | `ed8` first; the integration is machine-independent |
| Protocol | OpenAI-compatible is the default everywhere; the Anthropic protocol is a per-endpoint option for harnesses that prefer it |
| AccessKey credential for quota and billing reads | Acceptable, and optional |
| Architecture | Approach 2: two native integrations sharing one committed catalog |
| Priority | Agent Routing and the broker equally |

## 2. Non-goals

- Batch, async-task, file, image, video, speech, and realtime APIs. The catalog records these
  models' existence and eligibility, but no Pitwall surface calls them.
- The Responses API and Token Plan Harness tools (web search, code interpreter).
- Team Edition seat management and shared packs. The configuration accepts `team`, and quota reads
  use the same OpenAPI, but no seat-administration surface is built.
- The free-tier gateway. Token Plan is paid and bars application backends; the new-user free quota
  is not part of this work.
- Live catalogue scraping. The catalog is curated from documentation and changed by review.

## 3. Architecture

```text
            packages/agent-routing/runtime/model_routing/resources/config/model-studio.json
                              (canonical catalog)          ▲ byte-identical copy, drift test
                                        │                   │
      Agent Routing (stdlib)            │        src/pitwall/providers/model_studio/catalog.json
      ─ endpoint kind "model-studio"    │        Broker
      ─ routes add --model-studio       │        ─ ProviderType.MODEL_STUDIO + ModelStudioProvider
      ─ routes sync per harness         │        ─ OpenAI proxy through openai_base_url
      ─ concurrency cap, quota readiness│        ─ Credits quota window / per-token pricing
      ─ Token Plan automation gate      │        ─ Token Plan automation gate
                   │                    │                   │
                   └──── direct HTTPS to Model Studio ──────┘
```

The two halves never import each other. Each reads its own copy of the catalog, validates
configuration with the same rules, and talks to Model Studio directly. When the broker runs, the
existing Pitwall-origin route path (`routes add --from-pitwall`) also works for Model Studio
capabilities, because the broker's OpenAI proxy forwards to any provider that has an
`openai_base_url` and a `credential_ref`.

## 4. The shared catalog

File: `model-studio.json`, schema version 1. The canonical copy lives in Agent Routing's resources;
the broker ships a byte-identical copy. A root test (`tests/test_model_studio_catalog_parity.py`)
fails when the two differ, and each package validates its copy's schema in its own tests.

Contents, each group carrying a `sources` list of documentation slugs and a `snapshot` date:

- `plans`:
  - `token-plan-personal`, `token-plan-team`: region `ap-southeast-1` only; key prefix `sk-sp-`;
    unit `credits`; cycle `30d-from-purchase`; base URLs `openai`
    (`https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1`), `anthropic`
    (`https://token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic`), `native`
    (`https://token-plan.ap-southeast-1.maas.aliyuncs.com/api/v1`); `exhaustion`: blocking, no pay-as-you-go overflow
    (`token-plan-personal-faq`).
  - `pay-as-you-go`: unit `usd`; cycle `calendar-month`; per-region endpoints (next item).
  - Personal tiers: `lite`, `essential`, `standard`, `pro` with monthly Credits and concurrent-agent
    ranges (`token-plan-overview`).
- `regions`: `cn-beijing`, `ap-southeast-1`, `us-east-1`, `eu-central-1`, `ap-northeast-1`,
  `cn-hongkong`, each with the workspace host template `{workspace}.{region}.maas.aliyuncs.com` and
  the legacy DashScope host where one exists (`regions`, `base-url`). The workspace host is the
  default for pay-as-you-go because the DashScope domains freeze on 2026-09-30.
- `models`: for each text model in the research report's model table: `contextTokens`,
  `maxInputTokens`, `maxInputTokensThinking`, `maxOutputTokens`, `maxReasoningTokens`, `inputs`,
  `reasoning` (`hybrid-on`, `hybrid-off`, `always`, `none`), `effort` (`param`, `values`,
  `default`), `maxTokensIncludesReasoning`, `eligibility` (`personal`, `team`, `payAsYouGo`), and
  pay-as-you-go prices for Singapore (the "International" scope) where the model page publishes
  them (`input`, `output`, `cachedInput` for implicit-cache hits, optional `tiers` above 256,000
  input tokens). Prices differ by region scope, so other regions carry no catalog price and a
  pay-as-you-go broker provider there must supply its own cost. deepseek-v4.1-flash records its
  busy-hour price (idle hours cost half). Non-text models appear with `eligibility` only.
- `errors`: the documented 429/401/403 signals, keyed by `code` and message fragment, each mapped to
  a Pitwall classification (section 7).

## 5. Configuration and credentials

The same settings in both halves. Agent Routing records them on the endpoint in `routes.json` (the
environment variables below only supply defaults when an endpoint is created); the broker reads
them from the provider's `config`, with the same environment variables as defaults. Only
credential names are ever stored, never values.

| Setting | Meaning | Required |
| --- | --- | --- |
| `MODEL_STUDIO_PLAN` | `token-plan-personal`, `token-plan-team`, or `pay-as-you-go` | yes |
| `MODEL_STUDIO_TIER` | Personal tier (`lite`, `essential`, `standard`, `pro`), for concurrency and Credits defaults | Token Plan |
| `MODEL_STUDIO_REGION` | Region id | pay-as-you-go |
| `MODEL_STUDIO_WORKSPACE_ID` | Workspace for the dedicated host | optional |
| `MODEL_STUDIO_RENEWS_ON` | Date (`YYYY-MM-DD`) of any Token Plan cycle start; cycles repeat every 30 days from it. Used when OpenAPI stats are unavailable | Token Plan |
| API key | by reference: routes use `apiKeyEnv`, providers use `credential_ref` (default name `MODEL_STUDIO_API_KEY`) | yes |
| `ALIBABA_CLOUD_ACCESS_KEY_ID`, `ALIBABA_CLOUD_ACCESS_KEY_SECRET` | AccessKey for quota and billing OpenAPI reads | optional |
| `MODEL_STUDIO_TOKEN_PLAN_AUTOMATION` | `accept` to allow non-interactive use of a Token Plan key (Agent Routing records it on the endpoint as `tokenPlanAutomation: "accept"`) | Token Plan automation |

Validation rules, identical in both halves and failing with named errors that never echo values:

1. A key beginning `sk-sp-` is valid only with a Token Plan plan and Token Plan base URLs, and a
   Token Plan plan requires such a key. Any other pairing is refused because Model Studio would
   otherwise bill the call as pay-as-you-go without warning (`token-plan-personal-faq`).
2. Token Plan requires region `ap-southeast-1`; pay-as-you-go requires a known region.
3. A model must exist in the catalog and be eligible for the configured plan; an ineligible model
   is refused before any call (`403 AccessDenied.Unpurchased` otherwise).
4. Non-interactive use of a Token Plan key (broker inference, headless dispatch, workflows) requires
   `MODEL_STUDIO_TOKEN_PLAN_AUTOMATION=accept`. Without it the refusal names the Token Plan terms
   and the setting. Interactive harness sessions a user starts themselves are not gated.

## 6. Agent Routing

**Endpoint kind.** `routes.json` endpoints gain an optional `kind: "model-studio"` with `plan`,
`region`, `workspace`, `protocol` (`openai` default, `anthropic`), `tier`, `concurrency`, and
`renewsOn`, `tokenPlanAutomation`. For
this kind `baseUrl` is derived from the catalog; the normalized file stores the derived value, and a
stored `baseUrl` that differs from it is refused. `apiKeyEnv` remains the only credential field.
Existing endpoints are unchanged (`routes.py` `_validate_endpoint`).

**Adding routes.** `routes add <name> --model <model> --endpoint <endpoint-name>`, when the endpoint
is a Model Studio endpoint, validates eligibility, pins the harness, and fills `limits` from the
catalog (`context` = `maxInputTokens`, `output` = `maxOutputTokens`), replacing the 32768/4096 sync
defaults that would otherwise apply. `routes add-model-studio-endpoint <name> --plan PLAN
[--api-key-env NAME] [--region REGION] [--model-studio-workspace ID] [--protocol openai|anthropic]
[--tier TIER] [--concurrency N] [--renews-on DATE] [--accept-token-plan-automation]` creates the
endpoint (`--workspace` already names the dispatch workspace mode).

**Effort.** Each route's `effort` is validated against the model's catalog vocabulary, extending
`validate_effort`. Sync writes per-model reasoning options where a harness supports them (OpenCode
model `variants`, selected by `--variant`; Pi `models.json` `reasoning`), and dispatch renders the
harness's own effort flag. Effort on the Anthropic protocol is refused.

**Sync per harness.** `plan_endpoint_sync` for OpenCode writes `@ai-sdk/openai-compatible` (or
`@ai-sdk/anthropic` for `protocol: anthropic`) with catalog limits and streaming usage enabled; Pi,
Cline, Hermes, and goose receive the derived base URL and limits through their existing sync paths;
Qwen Code receives the endpoint through its environment. Routes to `alibaba-token-plan/*` models
pin their harness so the Qwen harness's `*/qwen*` pattern cannot claim them.

**Concurrency.** Each dispatch through a Model Studio endpoint holds one of the endpoint's
`concurrency` slot locks (default: the tier's upper bound) in the state directory for its lifetime;
the kernel releases a crashed dispatch's lock. A dispatch that finds no free slot waits up to the
dispatch's own timeout and is then refused with `endpoint_busy`.

**Readiness.** `routes probe` and `doctor` report, for a Model Studio endpoint: plan and key pairing
valid, model eligible, the OpenAI-compatible `GET …/compatible-mode/v1/models` list reachable with the key and naming the model (the Token Plan host answers the native `/api/v1/models` with 404; live check, 2026-09-26), and Credits
remaining when the AccessKey is configured and the plan exposes stats. Without stats, readiness
reports the local exhaustion lockout and the next renewal date from `renewsOn`. The local lockout is
written when a dispatch's output carries the Token Plan exhaustion message; later dispatches through
that endpoint are refused with `credits_exhausted` until the renewal date.

**AccessKey signing.** A small stdlib implementation of ACS3-HMAC-SHA256 for the Model Studio
OpenAPI (`get-subscription-stats`), used only for reads.

## 7. Broker

**Provider type and adapter.** `ProviderType.MODEL_STUDIO` and `ProviderAdapterId.MODEL_STUDIO`,
registered in `create_default_registry`, following the Together adapter's structure:
`ModelStudioCredentials` (API key and timeout; plan, tier, region, workspace, model, and renewal
live in the provider's `config.model_studio`), `availability` from the OpenAI-compatible model list filtered by
plan eligibility, and `infer` over the OpenAI-compatible endpoint.
`openai_base_url` for a Model Studio provider is derived from the catalog, so the existing OpenAI
proxy and fallback chain serve it unchanged (`routing/openai.py` `openai_base_url_for_provider`).

**Requests.** `infer` always streams with `stream_options.include_usage`, because a non-streaming
call can stop at about 300 seconds and return truncated content with HTTP 200
(`qwen-api-via-openai-chat-completions`). Thinking parameters (`enable_thinking`,
`reasoning_effort`, `thinking_budget`) pass through at the top level after catalog validation;
`reasoning_effort` together with `thinking_budget` on qwen3.8 is refused.

**Usage and cost.**
- Pay-as-you-go: `per_token` pricing from the catalog; `reasoning_tokens` count as output and
  `cached_tokens` use the cached-input price. Context-tier prices use the request's input size.
  The admitted ceiling is the request's max-token field or, without one, the model's
  `maxOutputTokens` (the pricing's `default_max_output_tokens`). `infer` sends that ceiling as
  `max_completion_tokens`, which includes reasoning, or as `max_tokens` for models whose `max_tokens`
  already includes it, so reasoning can never exceed the admitted spend.
- Token Plan: `cost_mode: zero` in USD (the subscription is prepaid) with a Credits quota window in
  the existing quota table (new `free_type` `subscription-credits`): `budget_units` = the tier's Credits, `window_start` = the last renewal,
  `reset_at` = window start + 30 days, `tos_verdict` recording the automation acceptance. Credits
  used come from the OpenAPI stats when available; otherwise the window only records exhaustion.
  Without the automation acceptance the row's `tos_verdict` is `avoid`, so routing never selects
  the provider; the proxy skips it with a named error.

**429 classification.** Model Studio errors are classified by code before any message pattern:
- `Throttling.RateQuota`, `Throttling.BurstRate`, and `Throttling.AllocationQuota` /
  "Allocated quota exceeded" are per-minute limits: `rate_limit_exceeded` with a 60-second
  cooldown (`rate-limit`, `error-code`).
- The Token Plan message "Your token-plan quota has been exhausted" is `quota_exhausted` until the
  next renewal date.
- `BudgetLimitExceeded`, `PrepaidBillOverdue`, `PostpaidBillOverdue`, and `400 Arrearage` are
  billing states: the provider is locked out for one hour with reason `billing_state`, not
  rate-limited.

The lockout table honours an explicit reset time for every non-permanent reason (today it does so
only for `quota_exhausted`), which is what gives the 60-second cooldown.

The generic `classify_429` also changes: a bare "quota" no longer implies a monthly reset. Only
explicit monthly wording ("monthly", "this month", "per month") does; any other quota message is a
short `rate_limit_exceeded` backoff. This fixes a defect that locks any gateway provider until the
first of next month on a per-minute "quota" message (`providers/gateway.py`).

**Billing reads.** With the AccessKey configured, the quota poll records each pay-as-you-go
provider's month-to-date spend for its model from `GetBillingOverview` in the quota table
(`free_type` `pay-as-you-go`). This is not `ACTUAL_COST`: that capability maps billing to individual
workloads, and the billing API reports only aggregates by model, key, or workspace.

## 8. Error handling

| Condition | Where | Result |
| --- | --- | --- |
| Key/plan/URL mismatch | config validation, both halves | refused before any call, named error |
| Model not eligible for plan | route add, dispatch, capability registration | refused, names the plan |
| Token Plan automation not accepted | broker inference, headless dispatch, workflows | refused, names the setting |
| Per-minute 429 | broker, gateway classifier | 60 s backoff, provider stays healthy |
| Token Plan Credits exhausted | broker, routing readiness | locked until renewal date |
| Billing-state 429/400 | broker | provider unavailable with reason |
| 401 wrong base URL or region | probe, broker | `invalid_endpoint_pairing` |
| Stream ends without usage | broker | usage `unavailable`, cost by ceiling |

## 9. Testing

- Hermetic by default: a fake Model Studio server (OpenAI-compatible SSE with usage chunks, the
  documented 429/401/403 bodies, the OpenAI-compatible model list) in each package's test suite.
- Catalog: schema tests in each package; the root parity test; tests that every model referenced
  by eligibility lists exists.
- Validation rules: one test per rule and per refusal.
- `classify_429`: table tests over the documented messages, including the regression that a
  per-minute "quota" message no longer returns a monthly lockout.
- Live checks, opt-in and cheap (qwen3.8-flash): Agent Routing `routes probe`, one dispatch per
  synced harness (OpenCode, Qwen Code), one broker inference and one proxied request, and one
  OpenAPI stats read to settle whether Personal Edition exposes stats.

## 10. Documentation

- A new operator guide, `docs/operator/model-studio.md`: plans, configuration, the Token Plan
  terms and the automation setting, and setup for Agent Routing and the broker.
- Agent Routing `docs/routes.md` (endpoint kind, flags) and the broker provider-plugin SDLC doc.
- Support-matrix row, changelog entries in both packages.

## 11. Risks

- The Token Plan terms prohibit exactly the automated use this enables; the automation setting
  makes that an explicit per-installation choice, and the operator guide states the penalty
  (suspension or key banning).
- No published per-model Credit rates: Pitwall cannot predict Credits per request, only observe
  them through OpenAPI stats or the exhaustion error.
- Personal Edition OpenAPI stats are undocumented; the live check decides whether readiness can
  show remaining Credits.
- The documentation contradicts itself on devices, regions, and some Team models; the catalog
  records the model pages' values and cites them.
