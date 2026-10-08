# Free-pool benchmark dossier — 2026-09-10 (plan Task 17, research §14 Phase 0)

Live runs: 2026-09-23, 22:04–22:14 UTC and 22:45–22:55 UTC, from one workstation through the
loopback gateway (`packages/gateway`, route table `config/gateway-routes.json`). Cost: $0;
keyless pools only. The results below are the second run's. **No keyless pool can serve an
anonymous caller: all 36 are `kill`.**

## Method

- Pools: the synced catalog's `keyless` free-type rows (`config/gateway-catalog.json`), 36 pools.
- Command (the gateway token authenticates to the loopback gateway; each request names its
  pool in `x-pitwall-route`, as the broker does):

  ```
  PITWALL_GATEWAY_TOKEN=<token> uv run --frozen python tools/gateway/bench_free_pools.py \
    --pools keyless --minutes 10 --rpm 3 --out <results.md>
  ```

- Schedule: all pools concurrently, 3 requests/minute each for 10 minutes, 30 requests per
  pool. The gateway limits one token to 120 requests/minute, so 36 pools at 6 rpm would have
  measured the gateway's own 429s.
- Request: `max_tokens: 1` with a prompt unique to each request (`ping <uuid> #<n>`); latency is the
  full round trip through the gateway.
- The first run sent the same `ping` every time and scored Pollinations `deepseek`, `glm`, and
  `grok` 30/30. Afterwards only that exact prompt succeeded without a key; any other prompt got 401.
  Pollinations was serving `ping` from a cache. Its verdicts measured the cache, so the tool now
  varies the prompt and the second run replaced them.
- AI Horde rows use the pool's public anonymous key (`PITWALL_GATEWAY_KEY_AIHORDE_ANON`).
- Verdict rule (ADR 0007 Phase 0): `keep` iff `ok/requests >= 0.95` and
  `p95_ms <= 8000` and `cost_usd == 0`; anything else is `kill`.

## Why pools failed

One request per failing pool after the run, through the gateway, showed the upstreams' own
answers:

- Pollinations (every model): HTTP 401, "A valid API key is required". These models are no longer
  keyless.
- AI Horde: HTTP 406, "Model None not known!", also when called directly at
  `oai.aihorde.net` with the anonymous key.
- OVHcloud: HTTP 429, "API rate limit exceeded", for anonymous callers at 3 rpm; 1–11 of 30
  succeeded.
- uncloseai: HTTP 404, the model does not exist upstream.
- SparkDesk `lite`: the route requires a key (`upstream_key_missing`) although the catalog lists
  it as keyless.

## Results

| Provider | Model | Requests | OK | HTTP 429 | p50 ms | p95 ms | Cost (USD) | Verdict |
|---|---|---:|---:|---:|---:|---:|---:|---|
| gw-aihorde-cydonia-24b-v4-3 | aphrodite/TheDrummer/Cydonia-24B-v4.3 | 30 | 0 | 1 | 1141.5 | 2477.4 | 0 | kill |
| gw-aihorde-skyfall-31b-v4-2 | aphrodite/TheDrummer/Skyfall-31B-v4.2 | 30 | 0 | 0 | 1159.1 | 2572.1 | 0 | kill |
| gw-aihorde-gemma-4-31b | google/gemma-4-31b | 30 | 0 | 0 | 772.8 | 1883.6 | 0 | kill |
| gw-ovhcloud-mistral-small-3-2-24b-instruct-2506 | Mistral-Small-3.2-24B-Instruct-2506 | 30 | 11 | 19 | 459.6 | 676.0 | 0 | kill |
| gw-ovhcloud-qwen2-5-vl-72b-instruct | Qwen2.5-VL-72B-Instruct | 30 | 7 | 23 | 420.2 | 684.9 | 0 | kill |
| gw-ovhcloud-qwen3-6-27b | Qwen3.6-27B | 30 | 8 | 22 | 477.7 | 633.8 | 0 | kill |
| gw-ovhcloud-gpt-oss-120b | gpt-oss-120b | 30 | 1 | 29 | 430.9 | 625.5 | 0 | kill |
| gw-ovhcloud-gpt-oss-20b | gpt-oss-20b | 30 | 5 | 25 | 431.1 | 660.5 | 0 | kill |
| gw-pollinations-deepseek | deepseek | 30 | 0 | 0 | 366.4 | 736.7 | 0 | kill |
| gw-pollinations-gemini-flash-lite-3-1 | gemini-flash-lite-3.1 | 30 | 0 | 0 | 366.9 | 2270.5 | 0 | kill |
| gw-pollinations-gemini-large | gemini-large | 30 | 0 | 0 | 410.7 | 1032.6 | 0 | kill |
| gw-pollinations-gemini-search | gemini-search | 30 | 0 | 0 | 352.6 | 1822.9 | 0 | kill |
| gw-pollinations-glm | glm | 30 | 0 | 0 | 352.1 | 895.8 | 0 | kill |
| gw-pollinations-grok | grok | 30 | 0 | 1 | 367.4 | 2210.0 | 0 | kill |
| gw-pollinations-grok-large | grok-large | 30 | 0 | 1 | 347.8 | 2224.8 | 0 | kill |
| gw-pollinations-kimi | kimi | 30 | 0 | 3 | 370.0 | 2153.2 | 0 | kill |
| gw-pollinations-minimax | minimax | 30 | 0 | 3 | 326.1 | 2337.7 | 0 | kill |
| gw-pollinations-mistral | mistral | 30 | 0 | 4 | 333.5 | 1214.8 | 0 | kill |
| gw-pollinations-mistral-large | mistral-large | 30 | 0 | 3 | 347.6 | 2345.9 | 0 | kill |
| gw-pollinations-nova | nova | 30 | 0 | 3 | 368.7 | 2432.0 | 0 | kill |
| gw-pollinations-nova-fast | nova-fast | 30 | 0 | 2 | 336.6 | 2336.4 | 0 | kill |
| gw-pollinations-openai | openai | 30 | 0 | 1 | 370.2 | 2063.5 | 0 | kill |
| gw-pollinations-openai-fast | openai-fast | 30 | 0 | 1 | 386.6 | 2232.7 | 0 | kill |
| gw-pollinations-openai-large | openai-large | 30 | 0 | 1 | 375.0 | 1072.6 | 0 | kill |
| gw-pollinations-perplexity-fast | perplexity-fast | 30 | 0 | 1 | 404.5 | 2195.5 | 0 | kill |
| gw-pollinations-perplexity-reasoning | perplexity-reasoning | 30 | 0 | 1 | 341.0 | 2104.2 | 0 | kill |
| gw-pollinations-polly | polly | 30 | 0 | 2 | 189.4 | 555.8 | 0 | kill |
| gw-pollinations-qwen-coder | qwen-coder | 30 | 0 | 2 | 354.5 | 2220.0 | 0 | kill |
| gw-pollinations-qwen-coder-large | qwen-coder-large | 30 | 0 | 3 | 322.5 | 945.0 | 0 | kill |
| gw-pollinations-qwen-large | qwen-large | 30 | 0 | 3 | 318.6 | 1033.8 | 0 | kill |
| gw-pollinations-qwen-safety | qwen-safety | 30 | 0 | 1 | 325.2 | 2111.7 | 0 | kill |
| gw-pollinations-qwen-vision | qwen-vision | 30 | 0 | 2 | 308.4 | 890.2 | 0 | kill |
| gw-sparkdesk-lite | lite | 30 | 0 | 3 | 17.7 | 24.8 | 0 | kill |
| gw-uncloseai-hermes-3-llama-3-1-8b-fp8-dynamic | adamo1139/Hermes-3-Llama-3.1-8B-FP8-Dynamic | 30 | 0 | 22 | 184.5 | 360.6 | 0 | kill |
| gw-uncloseai-gemma4:31b | gemma4:31b | 30 | 0 | 19 | 174.9 | 391.8 | 0 | kill |
| gw-uncloseai-qwen3-6:27b | qwen3.6:27b | 30 | 0 | 20 | 211.1 | 295.9 | 0 | kill |

## Verdicts

Keep/kill per pool (`kill` rows flip `enabled: false` via `pitwall gateway sync --apply-verdicts`):

- gw-aihorde-cydonia-24b-v4-3: kill
- gw-aihorde-skyfall-31b-v4-2: kill
- gw-aihorde-gemma-4-31b: kill
- gw-ovhcloud-mistral-small-3-2-24b-instruct-2506: kill
- gw-ovhcloud-qwen2-5-vl-72b-instruct: kill
- gw-ovhcloud-qwen3-6-27b: kill
- gw-ovhcloud-gpt-oss-120b: kill
- gw-ovhcloud-gpt-oss-20b: kill
- gw-pollinations-deepseek: kill
- gw-pollinations-gemini-flash-lite-3-1: kill
- gw-pollinations-gemini-large: kill
- gw-pollinations-gemini-search: kill
- gw-pollinations-glm: kill
- gw-pollinations-grok: kill
- gw-pollinations-grok-large: kill
- gw-pollinations-kimi: kill
- gw-pollinations-minimax: kill
- gw-pollinations-mistral: kill
- gw-pollinations-mistral-large: kill
- gw-pollinations-nova: kill
- gw-pollinations-nova-fast: kill
- gw-pollinations-openai: kill
- gw-pollinations-openai-fast: kill
- gw-pollinations-openai-large: kill
- gw-pollinations-perplexity-fast: kill
- gw-pollinations-perplexity-reasoning: kill
- gw-pollinations-polly: kill
- gw-pollinations-qwen-coder: kill
- gw-pollinations-qwen-coder-large: kill
- gw-pollinations-qwen-large: kill
- gw-pollinations-qwen-safety: kill
- gw-pollinations-qwen-vision: kill
- gw-sparkdesk-lite: kill
- gw-uncloseai-hermes-3-llama-3-1-8b-fp8-dynamic: kill
- gw-uncloseai-gemma4:31b: kill
- gw-uncloseai-qwen3-6:27b: kill
