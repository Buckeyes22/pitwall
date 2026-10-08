# Alibaba Cloud Model Studio

Model Studio is a first-class inference provider in both halves of the stack: Agent Routing
dispatches harness sessions through `model-studio` endpoints in the `[agents.profiles]` tables of `pitwall.toml`, and the broker
serves `model_studio` providers seeded like any other. Both halves read one committed catalog (`src/pitwall/providers/model_studio/catalog.json`, the only
copy), so plans, regions, base URLs, model limits, prices, and error classifications agree
everywhere.

Only credential *names* are ever stored (`apiKeyEnv` on endpoints, `credential_ref` on
providers). Keys live in environment variables you own.

## Plans

| Plan | Key prefix | Regions | Billing unit | Cycle | Exhaustion |
| --- | --- | --- | --- | --- | --- |
| `token-plan-personal` | `sk-sp-` | `ap-southeast-1` only | Credits | 30 days | blocking — no overflow |
| `token-plan-team` | `sk-sp-` | `ap-southeast-1` only | Credits | 30 days | blocking — no overflow |
| `pay-as-you-go` | `sk-` | six regions (below) | USD | calendar month | billing state |

- **Token Plan Personal** tiers: `lite` (11,500 Credits, 1–2 concurrent), `essential` (25,500,
  2–3), `standard` (45,000, 3–4), `pro` (180,000 Credits, 6–8 concurrent agents). The tier sets
  the Credits budget and the concurrency slots Agent Routing derives.
- **Token Plan Team** tiers: `standard` (25,000), `advanced` (100,000), `premium` (250,000).
  Per-tier concurrency is not published; derive slots explicitly with `--concurrency`.
- **Token Plan is served only from Singapore** (`ap-southeast-1`). The catalog derives the base
  URL from the plan: `https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1`
  (OpenAI protocol), `/apps/anthropic` (Anthropic protocol), or `/api/v1` (native).
- **Pay-as-you-go** regions: `ap-southeast-1`, `cn-beijing`, `us-east-1`, `eu-central-1`,
  `ap-northeast-1`, `cn-hongkong`. A workspace id gives a dedicated
  `{workspace}.<region>.maas.aliyuncs.com` host; without one the region's shared host is used
  when it has one.
- **Prices differ by region scope.** The catalog prices only `ap-southeast-1`; configuring an
  unpriced region without an explicit `cost` block is refused (`unpriced`) rather than guessed.
- A cycle date (`renews_on`, `YYYY-MM-DD`) is any cycle start; cycles repeat every 30 days from
  it. Exhaustion locks routing until that renewal; Credits do not overflow into spend.

## The Token Plan terms

The Token Plan terms allow interactive use only. Quoting the refusal both halves emit:
*"the Token Plan terms allow interactive use only"*. The published penalty for automated
(non-interactive) use is suspension or banning of the key.

Because of that, non-interactive use — broker inference, headless dispatch, workflows — is
gated behind an explicit acceptance:

- Broker: set `MODEL_STUDIO_TOKEN_PLAN_AUTOMATION=accept` in the environment, or
  `model_studio.automation: "accept"` in the provider config.
- Agent Routing: create the endpoint with `--accept-token-plan-automation` (or set the same
  variable while creating it); the endpoint records `tokenPlanAutomation: "accept"`.

The refusal names the setting and the terms; it never echoes the key. Accepting records *your*
acceptance of the automation risk per installation — Pitwall does not assume it. Interactive
harness sessions you start yourself are never gated.

## Agent Routing setup

Create the endpoint, add routes on it, sync the harness, and probe:

```bash
pitwall agents profiles add-model-studio-endpoint ms \
  --plan token-plan-personal --tier pro --renews-on 2026-09-12 \
  --accept-token-plan-automation
pitwall agents profiles add flash --model qwen3.8-flash --endpoint ms --harness opencode
pitwall agents profiles sync --harness opencode
pitwall agents profiles probe flash
```

`pitwall agents profiles add-model-studio-endpoint <name>` flags: `--plan`, `--tier`, `--region`,
`--model-studio-workspace`, `--protocol openai|anthropic`, `--concurrency`, `--renews-on
YYYY-MM-DD`, `--api-key-env` (default `MODEL_STUDIO_API_KEY`), and
`--accept-token-plan-automation`. `MODEL_STUDIO_PLAN`, `MODEL_STUDIO_TIER`,
`MODEL_STUDIO_REGION`, `MODEL_STUDIO_WORKSPACE_ID`, and `MODEL_STUDIO_RENEWS_ON` supply
defaults for the matching flags.

`pitwall agents profiles add` without `--harness` pins the route to `defaults.endpointHarness` (`qwen` out of
the box); pass `--harness opencode` to pin OpenCode explicitly. On a `model-studio` endpoint
the route's limits and effort come from the catalog: `qwen3.8-flash` gets
`--limits context=991808,output=131072` derived automatically, and `--effort` must use the
model's own vocabulary (`xhigh, medium, low` for the qwen3.8 effort models; `high` is
rejected). Models without an effort control reject `--effort` entirely.

Endpoint fields (stored under `[agents.profiles.endpoints.<name>]` in `pitwall.toml`):

| Field | Meaning |
| --- | --- |
| `kind` | `model-studio` |
| `plan` | `token-plan-personal`, `token-plan-team`, or `pay-as-you-go` (required) |
| `tier` | plan tier; sets derived concurrency and the Credits fallback budget (Token Plan) |
| `region` | region id; Token Plan forces `ap-southeast-1` |
| `workspace` | workspace id for a dedicated pay-as-you-go host |
| `protocol` | `openai` (default) or `anthropic` — selects the derived base URL path |
| `concurrency` | explicit slot count; else derived from the tier |
| `renewsOn` | `YYYY-MM-DD` cycle start; lockouts lift here |
| `tokenPlanAutomation` | `accept` — records the automation-risk acceptance |
| `apiKeyEnv` | environment variable holding the key (required; never the value) |

The derived `baseUrl` is persisted into the normalized file and must re-validate: an edited
`baseUrl` that differs from the catalog derivation is refused on the next load.

The **anthropic protocol** pairs with the OpenCode harness only (other harnesses are refused)
and takes no `effort` (the Anthropic protocol path has no effort parameter; use protocol
`openai` for effort control).

**Concurrency and `endpoint_busy`.** Dispatch holds one `flock` slot per in-flight session
(capped by the tier derivation or `--concurrency`). When all slots are busy the dispatch fails
with `endpoint_busy` and exit code `75`; slots are freed by the kernel if a dispatch process
dies.

**Local exhaustion lockout.** A Token Plan 429 whose body says the quota is exhausted writes a
lockout and refuses dispatch until the renewal date. The lockout lives at
`$XDG_STATE_HOME/pitwall/agents/endpoint-slots/<endpoint>/exhausted.json`
(`~/.local/state/...` when `XDG_STATE_HOME` is unset); delete the file after the plan renews to
lift it early. `pitwall agents profiles probe` reports the endpoint `quota-exhausted` with the renewal date.

## Broker setup

Seed a provider like any other; the URL and cost are derived from the catalog:

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

The seed fills `config.model_studio` (plan, region `ap-southeast-1`, tier, model, renewal),
derives `config.openai_base_url` (a different persisted URL is refused by API validation), and
derives `config.cost` unless the seed sets one: Token Plan prices as `zero` (Credits, not
dollars), pay-as-you-go in Singapore prices as `per_token` with the model's catalog rates —
per-million input/output, `per_million_cached_input_tokens` when the model publishes a
cached-input price, `input_tiers` for context-length tiered pricing (e.g. `qwen3.7-plus` above
256,000 input tokens), and `default_max_output_tokens` as the usage-estimate ceiling.

Pay-as-you-go variant:

```yaml
  - name: ms-qwen38-flash-payg
    capability: "coding.chat"
    provider_type: "model_studio"
    adapter: "model_studio"
    credential_ref: "MODEL_STUDIO_API_KEY"
    priority: 60
    model_studio:
      plan: "pay-as-you-go"
      region: "ap-southeast-1"
      workspace: "my-workspace"
      model: "qwen3.8-flash"
```

**Quota rows.** The reconciler's Model Studio tick writes one window per provider into
`provider_quotas`, keyed by `free_type`:

| `free_type` | Plan | Window | Budget/usage | `tos_verdict` |
| --- | --- | --- | --- | --- |
| `subscription-credits` | Token Plan | OpenAPI stats window, else the 30-day cycle from `renews_on` | Credits total and used; budget from the tier when stats are unavailable | `caution` when automation is accepted, `avoid` when not |
| `pay-as-you-go` | pay-as-you-go | recurring monthly | month-to-date USD spend | `ok` |

`tos_verdict` is the operator-facing terms signal: `avoid` marks a Token Plan provider that
cannot legally run headless traffic until automation is accepted; `caution` marks one where it
is accepted and the remaining risk is commercial (Credits exhaustion blocks routing).

**AccessKey variables.** `ALIBABA_CLOUD_ACCESS_KEY_ID` and `ALIBABA_CLOUD_ACCESS_KEY_SECRET`
are optional and separate from the model key. They unlock read-only, ACS3-signed Model Studio
OpenAPI calls: `GetSubscriptionStats` (live Credits remaining and the true reset time for Token
Plan) and `GetBillingOverview` (month-to-date spend for pay-as-you-go). Without them the
Token Plan window falls back to the configured tier budget and `renews_on`, and the
pay-as-you-go spend row records `not-configured` in its evidence.

## Errors

Model Studio failures are classified by catalog table, not string guessing: the classifier
matches the (status, code-or-message) pairs below. The policy is that per-minute throttles cool
down quickly, billing states and exhaustion lock longer, and identity and eligibility errors
are configuration problems, not transience.

| Provider response | Classification | What happens | Operator action |
| --- | --- | --- | --- |
| 429 `Throttling.RateQuota` / `Throttling.BurstRate` / `Throttling.AllocationQuota`, or message "allocated quota exceeded" | `rate_limit` | 60-second backoff; provider stays healthy | Nothing; traffic resumes automatically |
| 429 `BudgetLimitExceeded` / `PrepaidBillOverdue` / `PostpaidBillOverdue`, 400 `Arrearage`, 403 `AllocationQuota.FreeTierOnly` | `billing_state` | Provider unavailable for one hour with reason `billing_state` | Fix the account budget or bill, then wait out or clear the lockout |
| 429 message "token-plan quota has been exhausted" | `credits_exhausted` | Locked until the renewal date (Agent Routing writes the local lockout file) | Wait for renewal, or delete the lockout file after topping up |
| 401 `InvalidApiKey`, or message "invalid access token or token expired" | `invalid_endpoint_pairing` | Refused; readiness fails | The key/plan/URL pairing is wrong — a `sk-sp-` key on a non-Token Plan URL, or a `sk-` key where a Token Plan key is required; fix the endpoint or key |
| 403 `AccessDenied.Unpurchased` / `Endpoint.AccessDenied` / `AccessDenied` | `model_not_eligible` | Refused before dispatch | The model is not in the configured plan; pick an eligible model or change plan |
| 404 `model_not_found` / `model_not_supported` | `model_not_found` | Refused | The model id is not served on this endpoint; check the catalog spelling |
| 503 `ModelUnavailable` | `unavailable` | 60-second cooldown | Retry later; check the Model Studio status page |

Config problems are refused before any request leaves the machine, in both halves, with named
errors: key/plan/URL mismatch (`key_plan_mismatch`), Token Plan region violation
(`region_not_allowed`), model not in the catalog or not eligible for the plan
(`model_not_in_catalog`, `model_not_eligible`), and Token Plan automation not accepted
(`automation_not_accepted`). Refusals name the setting to change and never echo key values.
