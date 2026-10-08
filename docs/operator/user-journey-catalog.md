# User-Journey Catalog

Core user journeys through Pitwall, expressed as testable contracts. This catalog is not
an exhaustive release acceptance matrix. Each hermetic journey is
automated in [`scripts/release/run-user-journeys.sh`](../../scripts/release/run-user-journeys.sh)
(the `Jnn` id below is the harness function name); live journeys require real RunPod credentials
and are covered by the [release testing checklist](release-testing-checklist.md) instead.

Hermetic journeys never use a real RunPod key and never create paid resources. They run against
the local test infrastructure (`docker-compose.testinfra.yml`) exactly the way the README tells a
new user to.

## Personas

- **Evaluator** — a stranger who cloned the repository and follows the README with no prior context.
- **Operator** — runs the services, onboards real capacity, and handles incidents.
- **Personal user** — serves one model on their own RunPod account with `pitwall setup`, `serve`, `status`, and `stop`, without a database.
- **API client** — a program consuming the REST API.
- **Agent client** — an LLM agent, or its author, consuming the MCP server or the Messages route.
- **Contributor** — runs the test suite and quality gates before sending a change.
- **Agent Routing user** — dispatches work to external agent harnesses through `pitwall agents`, its shims, profiles, channel, and workflows.

Each table lists a journey, the oracle its harness step asserts, and how to run it alone. A
journey id in the filter column runs in-process: `bash scripts/release/run-user-journeys.sh <id>`
(J28 also runs J29–J31, which share its test file). "Full run" journeys need the disposable
database and Redis and run only in the full harness.

## Hermetic journeys (automated)

### Evaluator

| Id | Journey | Expected outcome | Filter |
| --- | --- | --- | --- |
| J01 | README Quick Start, verbatim: sync → testinfra → env → `db migrate` → `init --non-interactive` → `pitwall-api` → dry-run `POST /v1/inference` | Response has `"dry_run": true` and `"selected_provider_id": "prov_demo_runpod_lb"` | full run |
| J02 | `init --from-seed` and fully-manual `init` flags | Both exit 0; capability + provider rows exist | full run |
| J03 | `pitwall seed seed/capabilities.yaml seed/providers.yaml --mark-healthy` | Exit 0; provider `healthy`; **no** warm-volume/pod path touched | full run |
| J09 | `pitwall dashboard` (TUI) boots in a pty | Survives the six-second boot window (timeout exit 124), nonempty terminal transcript, no traceback; screen navigation is covered separately | full run |
| J44 | `uv build`, then the wheel and the sdist each installed into a clean virtual environment outside the checkout | Every service entry point answers `--help` with no configuration; `models list` matches the checkout's dossiers; `gateway status` reads the packaged catalog lock and the route table resolves to the packaged copy; `db migrate`, `db status`, `config check`, and `init` run against the test database with packaged migrations; the installed API serves a dry run; the installed `pitwall mcp serve broker` completes the stdio handshake and lists the same tools as the checkout | full run |

### Operator

| Id | Journey | Expected outcome | Filter |
| --- | --- | --- | --- |
| J04 | Manual onboarding: `create-capability` → `register-endpoint` → `set-provider-health` | Each exits 0; provider resolvable and healthy | full run |
| J05 | `pitwall config check` with full env, then with `DATABASE_URL` removed | Exit 0, then fail-closed non-zero with a clear message | full run |
| J06 | DB lifecycle: `db status` → idempotent `db migrate` → `db reset` refused without `--force` → refused for a remote host → allowed locally with `--force` → re-migrate | Guarded reset refuses exactly as documented; migrations re-apply cleanly | full run |
| J12 | Admin API: no/wrong secret rejected; with secret create capability + provider, disable/enable, `audit-capability` | 401/403 fail-closed; authorized calls succeed | full run |
| J13 | Whole-API auth (`PITWALL_API_TOKEN`) | Data plane 401 without bearer, 200 with; health stays public | full run |
| J14 | Inbound rate limit (`PITWALL_INBOUND_RATE_LIMIT=3/60s`) | 429 with `Retry-After` after the burst | full run |
| J18 | Webhook receiver with `PITWALL_WEBHOOK_SECRET`: unsigned, signed, and replayed deliveries | 401 unsigned; 200 signed; duplicate flagged idempotent | full run |
| J19 | Reconciler: `check` mode with valid/invalid `REDIS_URL`; worker boot | Check exits 0/non-zero correctly; worker starts, connects DB pool, survives boot window | full run |
| J20 | Cost exporter boots; `GET /metrics` | `pitwall_` metrics exposed on the configured port | full run |
| J21 | Kill switch with admin secret in a dev environment (fake key, no pods) | Structured `KillReport`; API healthy afterwards | full run |
| J22 | `warm-volume --model <id> --volume-id <vol> --dry-run`, then a real catalogue launch | Dry-run exits 0 without RunPod calls; real launch verifies `/v1/models`, populates the attached cache volume, and immediately tears down its lease | full run |
| J23 | Seeded serve registry → `pitwall serve --dry-run` → fake pod launch and ready hook → fake `/v1/models` identity → OpenAI chat proxy → teardown/disarm | Ordered lifecycle states are asserted, the exact chat body reaches the fake pod, dry-run has no launch call, and no listener, database, Redis, or RunPod credentials are used | `J23` |
| J24 | Serve automation renewal, idle stop, and revival | Signed events pass | `J24` |
| J25 | Self-hosted probe, warm, proxy, eviction, re-warm | Ordered states pass | `J25` |
| J26 | Validate compose and required environment docs | Existing coverage passes | full run |
| J36 | Every `pitwall` command path, including `pitwall agents` (145) through the installed entry points in a throwaway home, plus every discovered argument surface (442) | Each surface is registered at its source line on a parser whose `--help` names it; each command exits as pinned with its JSON keys; destructive commands take their refusal path (`db reset` without `--force` leaves the schema intact); long-running commands stay up; an unknown flag prints usage and exits 2 (1 for the `pitwall` dispatcher) | full run |
| J37 | Every configuration surface (186): the 77 settings fields, the routing weights, and every `.env.example` key | Each field parses from the environment, applies from `pitwall.toml`, and the environment wins; an invalid value makes `pitwall config check` exit 78 naming the variable without echoing it; every documented key sets its fields or measurably changes the consumer that reads it; `PITWALL_BIND_IP` binds every published port in `docker compose config` | `J37` |
| J38 | Every console view (ten in registry mode, four in personal mode) with its source loaded, empty, and failing, at 100x30 and 160x45, then every app and view key binding | Each view shows its rows and summary, its empty-state text, or its named failure and never a traceback; view keys switch without stacking; `r` reloads the view source; provider probe and availability reach the source; help, command, and search overlays open, act, and dismiss; Escape returns from Pods and Routes; `q` and `ctrl+c` quit | `J38` |
| J42 | The Postgres restore drill with a URL-reserved password: `pg_dump` and `pg_restore` of the seeded schema into a fresh database, with the server's own client tools run inside the test Postgres container when the host's differ | Every table is restored with its row count and content checksum; the seeded capability, provider, and three workloads survive with the workloads checksum the source had; the drill never skips on a machine that has the test stack | full run |
| J43 | A database built from the `v0.1.0a2` migration set and seeded through that schema, then `pitwall db migrate` and `db status` from the working tree | Each later migration applies exactly once, `db status` reports none pending, a second migrate applies nothing, and every value the release stored survives | full run |

### Personal user

| Id | Journey | Expected outcome | Filter |
| --- | --- | --- | --- |
| J28 | `pitwall setup --yes` twice in a fresh home | Endpoint key 0600 in a 0700 state dir, unchanged on rerun; one profile export; backend reported as personal | `J28` |
| J29 | `pitwall serve` refusals: over cap, does not fit, unpriced, route taken, TTL within the startup budget, routing CLI missing | Exit 1 with `refused: <code>`; no pod created | `J28` |
| J30 | `pitwall serve` → `status` → `stop` against fake RunPod | Ready lease with endpoint and try line; v2 deadline script; CUDA set at or above the floor; no credential in output; route attached then removed; record stopped | `J28` |
| J31 | `pitwall status` after a pod vanishes and after a deadline passes | Vanished pod recorded `gone`; overdue pod terminated and recorded `stopped/terminated_late` | `J28` |
| J32 | Supervised gateway with a route table: one keyless route, one rate-limited route, then stop | Each route reaches its own upstream with its own model id and no key; the 429 keeps its reset headers and becomes a typed quota signal ~30 s ahead; stop leaves no listener | `J32` |

### API client

| Id | Journey | Expected outcome | Filter |
| --- | --- | --- | --- |
| J07 | Discovery: `/healthz`, `/health`, `/v1/health`, `/v1/capabilities[/name]`, `/v1/providers[/id][/health]`, `/docs`, `/openapi.json` | All 200 with expected fields | full run |
| J08 | Inference error shapes: unknown capability, malformed body | 404 and 422 with structured errors | full run |
| J15 | Budget gate: near-zero `PITWALL_MONTHLY_BUDGET_USD`, non-dry-run inference | 402 before any provider call | full run |
| J16 | OpenAI proxy safety: URL-injection path rejected; exhausted budget rejected pre-upstream | 4xx (never SSRF), 402 with zero upstream traffic | full run |
| J17 | Async job error shapes: unknown workload id on status/result/cancel | 404 structured errors | full run |
| J35 | Every operation in the live OpenAPI document against a real `pitwall-api` with one token per scope, a seeded disposable database, and fresh records per operation | Anonymous calls get 401 and wrong-scope calls 403 before any effect; each operation then succeeds with its pinned status and keys, or returns its pinned typed code (offline provider, no executable route, retryable teardown failure); the OpenAI passthrough reaches a local upstream | full run |

### Agent client

| Id | Journey | Expected outcome | Filter |
| --- | --- | --- | --- |
| J10 | MCP over stdio: initialize → list tools → `pitwall_list_capabilities` → `pitwall_submit_inference` (dry-run) | Server info `pitwall`; 81 tools discovered; demo capability and dry-run result returned | full run |
| J11 | Attempt unauthenticated MCP over SSE | Process fails closed with an explicit stdio-only message | full run |
| J33 | Streamed `/v1/messages` with tools, then a `tool_result` follow-up | Streamed `tool_use` block with reassembled `input_json_delta` and `stop_reason: tool_use`; the upstream receives OpenAI `tools`, the pinned model id, and the result as a `tool` message with the same id | `J33` |
| J34 | Every one of the 81 MCP tools over real stdio against a seeded disposable database, with valid then invalid input | Each valid call succeeds with its pinned top-level keys or returns its pinned typed code (live-provider tools offline, absent records); no generic failure; every invalid or undeclared argument is refused as `invalid_tool_arguments` without echoing input; a real spend under an exhausted budget is refused as `budget_rejected` with no workload recorded | full run |

### Contributor

| Id | Journey | Expected outcome | Filter |
| --- | --- | --- | --- |
| J27 | README unit and security testing commands | Exact commands pass | `J27` |

### Agent Routing user

| Id | Journey | Expected outcome | Filter |
| --- | --- | --- | --- |
| J39 | `pitwall agents profiles add` an endpoint route in a throwaway home against a loopback OpenAI-compatible server, then `pitwall agents profiles list`, `pitwall agents profiles probe`, `doctor --probe-routes`, a second `pitwall agents profiles add` of the same name, `pitwall agents profiles add --from-pitwall` onto it, and `pitwall agents profiles remove` | The route lists with its model and endpoint host and `pitwall.toml` holds only the key's variable name; the probe is `reachable` and lists the model; doctor passes `routes.<name>.probe` with no failures; a plain re-add replaces the route; `--from-pitwall` onto an existing name exits 2 with `route '<name>' already exists` and changes nothing; after removal the list is empty | `J39` |
| J40 | Each of the fourteen shims (thirteen provider shims and `route-shim`) against a fake harness binary, with a model flag, then an effort flag, then a harness that exits 3 | Each harness receives its adapter's pinned argv (defaults, pass-through model and effort flags, prompt by argument, file, or stdin); output ends `SHIM-DONE exit=0` and the `SHIM-RESULT` receipt parsed by `pitwall.agents.result:parse_shim_receipt` is the finished ledger record; the failing harness gives `SHIM-DONE exit=3` and an `error` receipt; `route-shim` resolves the named route to its harness and model | `J40` |
| J41 | The scripted channel and workflow exits, a workflow with one Pi task against a fake `pi`, a workflow naming `muse`, and `inbox`, `answer`, `steer`, and `runs stop` on a running dispatch | The Tier 1, steer, and unattended fan-out exits pass; the Pi workflow succeeds and `pi` receives the task's model and prompt; the `muse` workflow is refused before any run with `does not support workflow execution`; `inbox` lists the waiting ask, `answer` records the operator's choice and clears it, `steer` adds a scope steer, and `runs stop` adds a stop steer with the requested grace window | `J41` |

After the journeys, a full run ends with the matrix gate: it runs every test bound in
`release_acceptance/reviewed-bindings.json` and fails unless every discovered surface is bound to
a test that passed (see [Testing Strategy](../sdlc/17-testing-strategy.md#10-journeys-and-the-surface-matrix)).

## Live journeys (manual, real credentials required)

| Id | Persona | Journey | Covered by |
| --- | --- | --- | --- |
| L1 | Operator | Onboard a real LB endpoint end to end | [create-lb-endpoint](create-lb-endpoint.md) |
| L2 | Operator | Real vLLM endpoint + real inference round trip | [create-vllm-endpoint](create-vllm-endpoint.md) |
| L3 | Operator | Pod lease lifecycle on real GPU capacity (launch → renew → stop) | [serve quickstart, database path](serve-quickstart.md#with-a-database-configured) |
| L4 | Operator | 19-check audit with live key | [16-check-audit-procedure](16-check-audit-procedure.md) |

## Running

```bash
docker compose -f docker-compose.testinfra.yml up -d --wait
DATABASE_URL=postgresql://pitwall:pitwall@127.0.0.1:5444/pitwall_test \
REDIS_URL=redis://127.0.0.1:6380/0 \
  bash scripts/release/run-user-journeys.sh
```

The harness is destructive to the target database (it exercises the guarded `db reset`); point it
only at disposable local infrastructure. It exits non-zero if any journey fails and prints a
per-journey PASS/FAIL summary.

The printed artifact directory retains a separate `Jnn-step.out` file for each
checked command and a `steps.tsv` index with its expected-outcome verdict.
Later checks do not overwrite earlier command output, including the unit and
security suites in J27. An expected nonzero exit is recorded as a passing
negative check; it is not a successful command execution.

Journeys with an id in the Filter column run alone without the test infrastructure:

```bash
DATABASE_URL='' REDIS_URL='' uv run --frozen bash scripts/release/run-user-journeys.sh J38
```
