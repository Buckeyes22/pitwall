# Model Studio live checks (2026-09-26)

Opt-in live checks from Task 19 of
[`docs/superpowers/plans/2026-09-26-model-studio-integration.md`](../superpowers/plans/2026-09-26-model-studio-integration.md),
run on branch `feat/model-studio` at commit `f332554` against Alibaba Model Studio with the
maintainer's Token Plan key. Every model call used `qwen3.8-flash` with the one short prompt
defined by the check ("Reply with the single word: pong").

Setup applied to every command, so the maintainer's real Agent Routing state was untouched:
`MODEL_STUDIO_API_KEY` was exported from the operator's local Token Plan key store, and
`XDG_CONFIG_HOME`, `XDG_STATE_HOME`, and `SUBAGENT_MODEL_ROUTING_ROUTES` pointed into a
disposable `/tmp/ms-live` tree (deleted after the run).

## Step 1: live test module

`tests/live/test_model_studio_live.py` follows the plan's snippet with two adjustments needed to
satisfy the current code: the `Provider` record also carries the required `priority` and
`updated_at` fields, and the inference test prints the streamed usage so the run log holds the
token counts.

## Step 2: commands, exit codes, and output

The plan's pytest command is missing the marker selection the repo's pytest defaults require:
`addopts` is `-m 'not live'`, so the command as written deselected the module. It was rerun with
`-m live`, the flag the `live` marker's own help text documents for selecting these tests.

| Command | Exit | Relevant output |
| --- | --- | --- |
| `PITWALL_MODEL_STUDIO_LIVE=1 uv run pytest tests/live/test_model_studio_live.py --run-live -q` | 5 | `2 deselected in 0.02s` (repo default `-m 'not live'`) |
| `PITWALL_MODEL_STUDIO_LIVE=1 uv run pytest tests/live/test_model_studio_live.py --run-live -m live -q` | 0 | `.s` then `1 passed, 1 skipped in 1.79s` |
| `... pytest tests/live/test_model_studio_live.py::test_live_subscription_stats_or_documented_absence --run-live -m live -q -rs` | 0 | `SKIPPED [1] tests/live/test_model_studio_live.py:62: AccessKey not configured or Personal Edition exposes no stats` |
| `... pytest tests/live/test_model_studio_live.py::test_live_inference_streams_usage --run-live -m live -q -s` | 0 | `HTTP/1.1 200 OK` from `chat/completions`; `usage: prompt_tokens=32 completion_tokens=1`; `1 passed` |

Agent Routing commands (run from `packages/agent-routing` with the sandbox environment above):

| Command | Exit | Relevant output |
| --- | --- | --- |
| `.venv/bin/python scripts/pitwall-agent-routing routes add-model-studio-endpoint ms-live --plan token-plan-personal --tier pro --accept-token-plan-automation` | 0 | `saved endpoint ms-live to /tmp/ms-live/config/subagent-model-routing/routes.json` |
| `.venv/bin/python scripts/pitwall-agent-routing routes add ms-flash --model qwen3.8-flash --endpoint ms-live --harness opencode` | 0 | `saved ms-flash to /tmp/ms-live/config/subagent-model-routing/routes.json` |
| `.venv/bin/python scripts/pitwall-agent-routing routes sync --harness opencode --yes` | 0 | `wrote /tmp/ms-live/config/opencode/opencode.json` |
| `routes probe ms-flash` (first attempt, key not exported) | 1 | `ms-flash: misconfigured — missing_key: export MODEL_STUDIO_API_KEY` |
| `routes probe ms-flash` (key exported) | 1 | `ms-flash: down — HTTP 404 from the Model Studio models API` |
| `bash scripts/route-shim.sh ms-flash /tmp/ms-pong.md` | 0 | `route-shim: ms-flash -> opencode qwen3.8-flash (endpoint <redacted host>)`; `pong`; `SHIM-DONE exit=0` |
| `bash scripts/route-shim.sh ms-flash@qwen /tmp/ms-pong.md` | 0 | `route-shim: ms-flash -> qwen qwen3.8-flash (endpoint <redacted host>)`; `pong`; `SHIM-DONE exit=0` |

Both dispatch run records in the sandbox state store show `"status": "succeeded"` for their
respective harnesses (`opencode` and `qwen`) on model `qwen3.8-flash`.

### Probe 404 observation (recorded, not fixed)

The readiness probe expects `GET <native>/models?model=` to list the model; the Token Plan
deployment answers that path with HTTP 404 and an empty body, so the probe reports `down` even
though dispatch works. A supplemental `curl` check (not a plan command) confirmed the deployment's
behaviour:

- `GET <native>/api/v1/models` (with and without the `model` query parameter) → HTTP 404, empty body.
- `GET <compatible-mode>/v1/models` → HTTP 200, 15 models listed, `qwen3.8-flash` among them.

The stored endpoint `baseUrl` is the OpenAI-compatible one and dispatch uses it, so only the
readiness probe's native-path assumption misses. The probe code is outside this task's file
ownership; no correction was made.

## Personal Edition stats

Not observed: only the Token Plan key is configured on this machine (no AccessKey pair), so
`get_subscription_stats` correctly returned `None` and the test took its documented-absence skip
path. Whether the Personal Edition exposes stats remains undetermined.

## Corrections

None. `GetSubscriptionStats`/`GetBillingOverview` were not exercised against the live service
because no AccessKey is configured, so no request-encoding rejection occurred and neither
`src/pitwall/providers/model_studio/openapi.py` nor
`packages/agent-routing/runtime/model_routing/model_studio_openapi.py` was changed.

## Not run: proxied request through the broker

The plan's final check (one proxied `POST /v1/chat/completions` through a broker seeded with the
Task 17 operator-guide YAML, expecting HTTP 200) was not run: `PITWALL_API_URL` is unset in this
environment, so no broker was reachable at it and none was seeded with the operator-guide YAML.
Per the task instructions no services were started and the running local containers were left
untouched.

## Cleanup

The disposable `/tmp/ms-live` tree and `/tmp/ms-pong.md` were deleted after the run; the plan's
Step 4 routes.json cleanup does not apply because all route changes landed in the sandbox.
